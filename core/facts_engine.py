#!/usr/bin/env python3
"""
Facts Engine for centsible.
Manages repository-specific micro-facts and conditional ephemeral injections.
"""

import functools
import hashlib
import json
import os
import re
import shlex
import sys
import unicodedata
from contextlib import contextmanager, ExitStack
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_SAVED_TOKENS_PER_HIT = 1500
DEFAULT_SAVED_TOKENS_PER_LOOP_STOP = 2000
DEFAULT_USD_PER_MTOK = 3.0


def normalize_text(text: str) -> str:
    """Normalize Unicode to NFC (fixes macOS NFD Vietnamese input mismatches)."""
    return unicodedata.normalize("NFC", text) if text else ""


def fold_vietnamese(text: str) -> str:
    """Strip Vietnamese diacritics (including đ/Đ) for accent-insensitive fallback matching."""
    if not text:
        return ""
    nfd = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    return "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")


WILDCARD_SPAN = 80


def bound_wildcards(pattern: str, span: int = WILDCARD_SPAN) -> str:
    """Rewrite unbounded `.*`/`.+` (outside escapes and [classes]) to `.{0,N}`/`.{1,N}`.

    Without this, `a.*b` matches any long prompt (or chat history) containing `a` and `b`
    anywhere, so facts fire on unrelated prompts.
    """
    out: List[str] = []
    i, n, in_class = 0, len(pattern), False
    while i < n:
        c = pattern[i]
        if c == "\\" and i + 1 < n:
            out.append(pattern[i:i + 2])
            i += 2
        elif in_class:
            in_class = c != "]"
            out.append(c)
            i += 1
        elif c == "[":
            j = i + 1 + (pattern[i + 1:i + 2] == "^")
            if pattern[j:j + 1] == "]":  # leading ] is a literal member
                j += 1
            out.append(pattern[i:j])
            i, in_class = j, True
        elif c == "." and pattern[i + 1:i + 2] in ("*", "+"):
            out.append(".{%d,%d}" % (0 if pattern[i + 1] == "*" else 1, span))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def debug_warn(where: str, exc: BaseException) -> None:
    """Hooks must never break the agent, so failures are swallowed; set CENTSIBLE_DEBUG=1 to see them on stderr."""
    if os.environ.get("CENTSIBLE_DEBUG"):
        sys.stderr.write(f"[centsible:debug] {where}: {type(exc).__name__}: {exc}\n")


@contextmanager
def file_lock(path: Path):
    """Exclusive cross-process lock around read-modify-write of `path` (sidecar `<name>.lock`).

    shortcut: best-effort, no-op where fcntl is missing (Windows) or the dir is unwritable.
    """
    try:
        import fcntl
        fh = open(path.with_name(path.name + ".lock"), "a")
    except (ImportError, OSError):
        yield
        return
    try:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield
    finally:
        fh.close()  # closing releases the flock


def atomic_write_text(path: Path, text: str) -> None:
    """Write via a unique temp file + rename so readers never see a partial file."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    finally:
        if tmp.exists():
            tmp.unlink()


_PLAIN_TERM_RE = re.compile(r"[\w\-./: ]+")


def _word_bound(pattern: str) -> str:
    """Make plain-word patterns (or `a|b` lists of them) whole-token: `pip` must not hit `PIPE`/`pipeline`.

    Only edges that are word characters get a boundary, so `auth/` still matches `auth/login.py`.
    Patterns using regex syntax are left untouched.
    """
    parts = pattern.split("|")
    if not all(_PLAIN_TERM_RE.fullmatch(p) for p in parts):
        return pattern
    return "|".join(
        (r"(?<!\w)" if re.match(r"\w", p[0]) else "") + p + (r"(?!\w)" if re.match(r"\w", p[-1]) else "")
        for p in parts
    )


def regex_matches(pattern: str, text: str) -> bool:
    """Match regex against text with NFC normalization and Vietnamese accent-insensitive fallback."""
    norm_pat = _word_bound(bound_wildcards(normalize_text(pattern)))
    norm_txt = normalize_text(text)
    if re.search(norm_pat, norm_txt, re.IGNORECASE):
        return True
    folded_pat = fold_vietnamese(norm_pat)
    folded_txt = fold_vietnamese(norm_txt)
    return bool(re.search(folded_pat, folded_txt, re.IGNORECASE))


def estimate_tokens(text: str) -> int:
    """Estimate LLM token count (~4 UTF-8 bytes per token; accurate for both English & Vietnamese)."""
    if not text:
        return 0
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


_VAL = r"""(?:"[^"]*"|'[^']*'|\S+)"""
_SECRET_NAME = r"[\w-]*(?:token|secret|passw(?:or)?d|pwd|api[-_]?key|access[-_]?key|private[-_]?key|auth(?:orization)?|credential)[\w-]*"
_MASK_VALUE = lambda m: m.group("k") + m.group("sep") + "***"
_SECRET_RES = [
    (re.compile(r"\b(Bearer|Basic)\s+[\w.~+/=-]+", re.IGNORECASE), lambda m: m.group(1) + " ***"),
    # flags may take their value after a space (--password abc); bare names only after = or : (prose stays intact)
    (re.compile(rf"(?<![\w-])(?P<k>--?{_SECRET_NAME})(?P<sep>=|\s+)(?P<v>{_VAL})", re.IGNORECASE), _MASK_VALUE),
    (re.compile(rf"(?<![\w-])(?P<k>{_SECRET_NAME})(?P<sep>=|:\s*)(?P<v>{_VAL})", re.IGNORECASE), _MASK_VALUE),
    (re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+@"), lambda m: "***@"),  # URL credentials
    # well-known token shapes, wherever they appear
    (re.compile(r"\b(?:sk-[\w-]{8,}|gh[pousr]_\w{20,}|xox[abprs]-[\w-]{10,}|AKIA[0-9A-Z]{16}|eyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]+)"), lambda m: "***"),
]


def redact_secrets(text: str) -> str:
    """Mask credentials (secret-named flags/env vars, bearer tokens, URL creds, known token shapes) before a
    command or prompt is persisted into facts.jsonl, which often sits in a repo and gets committed.

    shortcut: pattern-based, misses secrets with no telltale name or shape; upgrade to an entropy check if that bites.
    """
    for rx, repl in _SECRET_RES:
        text = rx.sub(repl, text)
    return text


def is_fact_missing_in_command(fact_text: str, cmd_text: str) -> bool:
    """
    Check if a micro-fact prescribes specific CLI flags (e.g. --compact, -q)
    or ASCII CLI subcommands in quotes that are missing from cmd_text.
    """
    if not fact_text or not cmd_text:
        return False
    # Ignore negative clauses like "instead of '...'" so failed commands/flags aren't required
    prescribed_text = re.split(r"\binstead of\b", fact_text, flags=re.IGNORECASE)[0]
    flags = re.findall(r"(?:^|\s)(--[a-zA-Z0-9_-]+|-[a-zA-Z0-9])\b", prescribed_text)
    flags = [fl.strip() for fl in flags if fl.strip()]
    if flags:
        return any(fl not in cmd_text for fl in flags)
    quoted = [
        q for q in re.findall(r"['\"`]([^'\"`]{2,40})['\"`]", prescribed_text)
        if q.isascii()
    ]
    if quoted:
        return any(q not in cmd_text for q in quoted)
    return False


def get_default_facts_path(workspace_paths: Optional[List[str]] = None) -> Path:
    """Determine the appropriate facts.jsonl path."""
    candidates: List[Path] = []
    if workspace_paths:
        for ws in workspace_paths:
            try:
                ws_path = Path(ws).resolve()
                if ws_path.is_dir():
                    candidates.append(ws_path)
            except Exception as e:
                debug_warn("get_default_facts_path(workspace)", e)

    try:
        cwd_path = Path.cwd().resolve()
        if cwd_path not in candidates:
            candidates.append(cwd_path)
    except Exception as e:
        debug_warn("get_default_facts_path(cwd)", e)

    # 1. Project-local directory (.agents/ or .claude/ or root facts.jsonl)
    home = Path.home().resolve()
    for base in candidates:
        # ~/.agents and ~/.claude are global tool dirs, not a project workspace
        if base == home:
            continue
        for folder in (".agents", ".claude"):
            local_dir = base / folder
            if local_dir.exists() and local_dir.is_dir():
                return local_dir / "facts.jsonl"
        root_facts = base / "facts.jsonl"
        if root_facts.exists():
            return root_facts

    # 2. Global fallback, scoped per project so one repo's facts never leak into another
    env_home = os.environ.get("CENTSIBLE_HOME")
    global_dir = Path(env_home).expanduser().resolve() if env_home else (Path.home() / ".centsible")
    base = candidates[0] if candidates else home
    root = next((p for p in (base, *base.parents) if (p / ".git").exists()), base)
    key = f"{root.name}-{hashlib.sha1(str(root).encode('utf-8')).hexdigest()[:8]}"
    return global_dir / "projects" / key / "facts.jsonl"


def get_global_facts_path() -> Path:
    """Facts that apply to every repo: ~/.centsible/facts.jsonl (or $CENTSIBLE_HOME/facts.jsonl)."""
    env_home = os.environ.get("CENTSIBLE_HOME")
    return (Path(env_home).expanduser().resolve() if env_home else Path.home() / ".centsible") / "facts.jsonl"


COMMON_BINS = {
    # Python
    "pytest", "uv", "poetry", "ruff", "mypy", "pip", "flake8", "black", "alembic", "tox",
    # JS / TS
    "npm", "pnpm", "yarn", "bun", "npx", "vitest", "jest", "playwright", "tsc", "eslint", "biome",
    # Build / API / Infra / DB
    "bruno", "cargo", "go", "docker", "make", "just", "gradle", "mvn", "dotnet", "prisma", "terraform", "kubectl",
}
_RUNNER_BINS = {"uv", "poetry", "npx", "bun", "pnpm", "yarn", "npm"}
# Interpreters are too generic to learn on; the script they run ('node scripts/x.js') is the real tool.
_INTERPRETERS = {"node", "python", "python3", "bash", "sh", "deno", "ruby"}
_SCRIPT_RE = re.compile(r"\.(?:js|mjs|cjs|ts|py|sh|rb)$")
_SHELL_OPS = {"&&", "||", ";", "|"}


def _split_shell_segments(cmd: str) -> List[List[str]]:
    try:
        tokens = shlex.split(cmd, posix=True)
    except ValueError:
        tokens = cmd.split()
    segments: List[List[str]] = []
    current: List[str] = []
    for tok in tokens + [";"]:
        if tok in _SHELL_OPS:
            if current:
                segments.append(current)
                current = []
        else:
            current.append(tok)
    return segments


def find_common_bin(cmd: str) -> Optional[str]:
    """Return the first known CLI tool near the start of any shell segment, skipping flags and wrappers."""
    for segment in _split_shell_segments(cmd):
        matched = [Path(part).name for part in segment[:6] if Path(part).name in COMMON_BINS]
        if matched:
            if matched[0] in _RUNNER_BINS and len(matched) > 1:
                return matched[1]
            return matched[0]
    for segment in _split_shell_segments(cmd):
        for idx, part in enumerate(segment[:6]):
            if Path(part).name in _INTERPRETERS:
                script = next((t for t in segment[idx + 1 : idx + 6] if _SCRIPT_RE.search(t)), None)
                if script:
                    return Path(script).name
    return None


def _extract_invocation_signature(cmd: str, target_bin: str) -> Optional[tuple]:
    """
    Extract how `target_bin` is invoked (runner prefix, binary path, flags, and non-path subcommands)
    so commands that only differ by target file path or `cd` directory are not treated as CLI recoveries.
    """
    for segment in _split_shell_segments(cmd):
        for idx, part in enumerate(segment[:6]):
            if Path(part).name == target_bin:
                prefix_and_bin = tuple(segment[: idx + 1])
                rest = segment[idx + 1 :]
                flags = tuple(t for t in rest if t.startswith("-"))
                subcommands = tuple(
                    t for t in rest
                    if not t.startswith("-")
                    and "/" not in t
                    and not re.search(r"\.[a-zA-Z0-9]{1,6}$", t)
                )
                return (prefix_and_bin, flags, subcommands)
    return None


_QUESTION_START_RE = re.compile(
    r"^\s*(?:tại sao|vì sao|làm sao|như thế nào|có nên|khi nào|ở đâu|ai\b|cái gì|"
    r"why|how|what|when|where|who|should|can|could|would)\b",
    re.IGNORECASE,
)

_CORRECTION_TRIGGER_RE = re.compile(
    r"(?:^|\b)(?:"
    r"không phải|sai rồi|nhầm rồi|"
    r"đừng\s+(?:dùng|sửa|chạy|xóa|gọi|đụng|thay đổi)|"
    r"tuyệt đối không|cấm\s+(?:dùng|sửa|chạy|xóa)|"
    r"lưu ý(?:\s*:|\s+là)?|nhớ\s+là|quy tắc(?:\s*:|\s+là)?|"
    r"bắt buộc\s+phải|luôn\s+phải|mặc định\s+(?:là|phải)|"
    r"no,\s*(?:use|don't|do not|never|always)|"
    r"don't\s+(?:use|edit|modify|touch|run)|"
    r"do not\s+(?:use|edit|modify|touch|run)|"
    r"never\s+(?:use|edit|modify|touch|run)|"
    r"always\s+(?:use|run|keep|pass)|"
    r"remember\s+(?:to|that)|gotcha:|rule:"
    r")",
    re.IGNORECASE,
)


def _extract_correction_targets(prompt: str) -> List[str]:
    """Extract technical identifiers (files, backticked symbols, known CLI tools, CamelCase/hyphenated modules)."""
    targets: List[str] = []

    def _add(tok: str) -> None:
        clean = tok.strip().strip("`'\".,;:()[]{}")
        if clean and len(clean) >= 2 and clean not in targets:
            targets.append(clean)

    # 1. File names with common code/config extensions
    for m in re.finditer(
        r"\b([a-zA-Z0-9_.-]+\.(?:py|ts|tsx|js|jsx|rs|go|java|kt|rb|php|sql|prisma|json|yaml|yml|toml|sh|md|vue|svelte))\b",
        prompt,
    ):
        _add(m.group(1))

    # 2. Backticked identifiers (single token)
    for m in re.finditer(r"`([a-zA-Z0-9_./-]+)`", prompt):
        _add(m.group(1))

    # 3. CamelCase class/component identifiers (e.g. AnimeLayerCompositor)
    for m in re.finditer(r"\b([A-Z][a-z0-9]+(?:[A-Z][a-zA-Z0-9]+)+)\b", prompt):
        _add(m.group(1))

    # 4. Known CLI binaries in COMMON_BINS
    for word in re.findall(r"\b[a-zA-Z0-9_-]+\b", prompt):
        if word.lower() in COMMON_BINS:
            _add(word.lower())

    # 5. Hyphenated or snake_case module/feature identifiers (e.g. video-agent, floating_cards)
    for m in re.finditer(r"(?<![-\w])([a-zA-Z][a-zA-Z0-9]*(?:[-_][a-zA-Z0-9]+)+)\b", prompt):
        _add(m.group(1))

    return targets


def detect_user_correction(prompt: str) -> Optional[str]:
    """
    Detect if a user prompt states a correction, rule, or gotcha.
    Returns a concise capture nudge if detected, or None.
    """
    if not prompt or "?" in prompt:
        return None
    norm = normalize_text(prompt.strip())
    if len(norm) < 12 or _QUESTION_START_RE.search(norm):
        return None
    if not _CORRECTION_TRIGGER_RE.search(norm):
        return None
    targets = _extract_correction_targets(norm)
    if not targets:
        return None
    suggested_pattern = "|".join(targets[:2])
    return (
        f"[Centsible Correction Detected] Persist this rule/gotcha if verified: "
        f"`centsible facts add --pattern \"{suggested_pattern}\" --fact \"<concise_rule>\" --category gotcha`"
    )


def _facts_locked(fn):
    """Serialize a FactsEngine read-modify-write on facts.jsonl across hook processes."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with ExitStack() as stack:  # fixed order (project, then global) so processes never deadlock
            for path in (self.storage_path, self.global_path):
                if path:
                    stack.enter_context(file_lock(path))
            return fn(self, *args, **kwargs)
    return wrapper


def derive_summary(base: Dict[str, Any], usd_per_mtok: float = DEFAULT_USD_PER_MTOK) -> Dict[str, Any]:
    """Add ROI / cost / rate fields to summed counters (one project, or several added together)."""
    saved_loop = base["saved_loop_tokens"]
    gross_saved = base["saved_discovery_tokens"] + saved_loop
    net_saved = gross_saved - base["injected_tokens"]
    saved_usd = (net_saved / 1_000_000.0) * usd_per_mtok
    total_calls = base["tool_calls_total"]
    first_try_rate = (base["first_try_successes"] / total_calls * 100.0) if total_calls > 0 else 100.0
    error_rate = (base["tool_calls_failed"] / total_calls * 100.0) if total_calls > 0 else 0.0
    roi = (gross_saved / base["injected_tokens"]) if base["injected_tokens"] > 0 else 0.0
    return {
        **base,
        "gross_saved_tokens": gross_saved,
        "net_saved_tokens": net_saved,
        "saved_usd": round(saved_usd, 4),
        "saved_cents": round(saved_usd * 100.0, 2),
        "first_try_success_rate": round(first_try_rate, 1),
        "tool_error_rate": round(error_rate, 1),
        "roi_multiplier": round(roi, 1),
        "usd_per_mtok": usd_per_mtok,
    }


class FactsEngine:
    def __init__(self, storage_path: Optional[Path] = None, global_path: Optional[Path] = None):
        self.storage_path = storage_path or get_default_facts_path()
        # Optional second layer shared by all repos; a project fact with the same id/pattern wins.
        self.global_path = global_path if global_path != self.storage_path else None
        self.metrics_path = self.storage_path.parent / "metrics.json"

    def load_metrics(self) -> Dict[str, int]:
        """Load repository-level token and execution telemetry."""
        defaults = {
            "prompt_injections": 0,
            "fact_hits": 0,
            "tool_hint_hits": 0,
            "injected_tokens": 0,
            "saved_discovery_tokens": 0,
            "loop_interventions": 0,
            "saved_loop_tokens": 0,
            "tool_calls_total": 0,
            "tool_calls_failed": 0,
            "first_try_successes": 0,
            "auto_learned_facts": 0,
        }
        if not self.metrics_path.exists():
            return defaults
        try:
            with open(self.metrics_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for k, v in defaults.items():
                    data.setdefault(k, v)
                return data
        except Exception as e:
            debug_warn("load_metrics", e)
            return defaults

    def update_metrics(self, **deltas: int) -> Dict[str, int]:
        """Atomically increment telemetry counters."""
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(self.metrics_path):
            metrics = self.load_metrics()
            for k, delta in deltas.items():
                if delta:
                    metrics[k] = metrics.get(k, 0) + int(delta)
            try:
                atomic_write_text(self.metrics_path, json.dumps(metrics, ensure_ascii=False, indent=2))
            except Exception as e:
                debug_warn("update_metrics", e)
        return metrics

    def get_metrics_summary(self, usd_per_mtok: float = DEFAULT_USD_PER_MTOK) -> Dict[str, Any]:
        """Compute ROI, token savings, and cents/USD saved."""
        m = self.load_metrics()
        facts = self.list_facts()
        # Reconcile with per-fact counters if facts were used before metrics.json existed
        fact_hits = max(m["fact_hits"], sum(f.get("hits", 0) for f in facts))
        injected_tokens = max(
            m["injected_tokens"],
            sum(f.get("injected_tokens", f.get("hits", 0) * (estimate_tokens(f.get("fact", "")) + 4)) for f in facts),
        )
        saved_discovery = max(
            m["saved_discovery_tokens"],
            sum(f.get("saved_tokens", f.get("hits", 0) * DEFAULT_SAVED_TOKENS_PER_HIT) for f in facts),
        )
        auto_learned_count = max(
            m["auto_learned_facts"],
            sum(1 for f in facts if f.get("category") in ("auto-learned", "gotcha") and str(f.get("id", "")).startswith("auto_")),
        )
        base = {
            **m,
            "auto_learned_facts": auto_learned_count,
            "total_facts": len(facts),
            "fact_hits": fact_hits,
            "injected_tokens": injected_tokens,
            "saved_discovery_tokens": saved_discovery,
        }
        return derive_summary(base, usd_per_mtok)

    @staticmethod
    def _read(path: Optional[Path]) -> List[Dict]:
        """Raw facts of one file; unreadable lines are skipped."""
        if not path or not path.exists():
            return []
        facts = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                try:
                    facts.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return facts

    def list_facts(self, category: Optional[str] = None, scope: Optional[str] = None) -> List[Dict]:
        """Facts from disk. scope='project'|'global' reads one file raw; default is the merged view
        (project + global, global ones tagged `_scope: global` and dropped when a project fact overrides them)."""
        if scope == "global":
            facts = self._read(self.global_path)
        elif scope == "project":
            facts = self._read(self.storage_path)
        else:
            facts = self._read(self.storage_path)
            ids = {f.get("id") for f in facts}
            pats = {f.get("pattern") for f in facts}
            facts += [
                dict(g, _scope="global") for g in self._read(self.global_path)
                if g.get("id") not in ids and g.get("pattern") not in pats
            ]
        return [f for f in facts if not category or f.get("category") == category]

    @_facts_locked
    def add_fact(
        self,
        pattern: str,
        fact: str,
        category: str = "general",
        fact_id: Optional[str] = None,
        est_savings: int = DEFAULT_SAVED_TOKENS_PER_HIT,
        scope: str = "project",
    ) -> Dict:
        """Add or update a micro-fact in the project file, or the global one with scope='global'."""
        path = self._scope_path(scope)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch(exist_ok=True)

        if len(pattern) > 500:
            raise ValueError("Regex pattern is too long (maximum 500 characters).")

        if pattern != "*":
            try:
                compiled = re.compile(pattern, re.IGNORECASE)
            except re.error as e:
                raise ValueError(f"Invalid regex pattern '{pattern}': {e}")
            if category != "always" and compiled.search(""):
                raise ValueError(
                    f"Pattern '{pattern}' matches empty text, so it would fire on every prompt "
                    "(use category 'always' for that)."
                )

        pattern = normalize_text(pattern)
        clean_fact = normalize_text(fact.strip().replace("\n", " "))
        if not clean_fact:
            raise ValueError("Fact content cannot be empty.")

        # Deterministic id: re-adding the same pattern+fact updates in place, a different fact on the
        # same pattern is kept alongside (builtin hash() is per-process random, time() collides within 1s).
        fact_id = fact_id or "fact_" + hashlib.sha1(f"{pattern}\0{clean_fact}".encode("utf-8")).hexdigest()[:10]

        new_entry = {
            "id": fact_id,
            "pattern": pattern,
            "fact": clean_fact,
            "category": category,
            "hits": 0,
            "injected_tokens": 0,
            "saved_tokens": 0,
            "est_savings": est_savings,
            "created_at": datetime.utcnow().isoformat() + "Z",
            "updated_at": datetime.utcnow().isoformat() + "Z",
        }

        facts = self._read(path)
        updated = False
        for i, existing in enumerate(facts):
            if existing.get("id") == fact_id:
                new_entry["hits"] = existing.get("hits", 0)
                new_entry["injected_tokens"] = existing.get("injected_tokens", 0)
                new_entry["saved_tokens"] = existing.get("saved_tokens", 0)
                new_entry["created_at"] = existing.get("created_at", new_entry["created_at"])
                facts[i] = new_entry
                updated = True
                break

        if not updated:
            facts.append(new_entry)

        self._write(path, facts)
        return new_entry

    @_facts_locked
    def remove_fact(self, fact_id_or_pattern: str, scope: str = "project") -> bool:
        """Remove a fact by ID or pattern from the project file (or the global one with scope='global')."""
        path = self._scope_path(scope)
        facts = self._read(path)
        initial_len = len(facts)
        facts = [
            f for f in facts
            if f.get("id") != fact_id_or_pattern and f.get("pattern") != fact_id_or_pattern
        ]
        if len(facts) < initial_len:
            self._write(path, facts)
            return True
        return False

    @_facts_locked
    def match(self, text: str, record_hits: bool = True) -> List[Dict]:
        """Match input text against all patterns. Returns matching facts."""
        if not text:
            return []

        facts = self.list_facts()
        matched = []
        needs_save = False
        total_injected_delta = 0
        total_saved_delta = 0

        for f in facts:
            pattern = f.get("pattern", "")
            if not pattern:
                continue
            is_always = (pattern == "*" or f.get("category") == "always")
            try:
                if is_always or regex_matches(pattern, text):
                    matched.append(f)
                    if record_hits:
                        tok_cost = estimate_tokens(f.get("fact", "")) + 4
                        tok_saved = f.get("est_savings", DEFAULT_SAVED_TOKENS_PER_HIT)
                        f["hits"] = f.get("hits", 0) + 1
                        f["injected_tokens"] = f.get("injected_tokens", 0) + tok_cost
                        f["saved_tokens"] = f.get("saved_tokens", 0) + tok_saved
                        f["last_hit_at"] = datetime.utcnow().isoformat() + "Z"
                        total_injected_delta += tok_cost
                        total_saved_delta += tok_saved
                        needs_save = True
            except re.error:
                continue

        if needs_save and record_hits:
            header_tokens = 24  # "[CENTSIBLE MICRO-SKILLS...]" header overhead
            self._save_merged(facts)
            self.update_metrics(
                prompt_injections=1,
                fact_hits=len(matched),
                injected_tokens=total_injected_delta + header_tokens,
                saved_discovery_tokens=total_saved_delta,
            )

        return matched

    @_facts_locked
    def match_tool_call(
        self,
        tool_call: Dict[str, Any],
        exclude_ids: Optional[List[str]] = None,
        only_missing_flags: bool = True,
        record_hits: bool = True,
    ) -> List[Dict]:
        """
        Match a tool call's command/arguments against stored micro-facts.
        Catches facts that were not triggered by the user prompt (e.g. user said 'fix bug'
        and agent runs 'bruno' or 'pytest' mid-turn without required flags, or edits a file
        governed by an architectural rule/gotcha).
        """
        args = tool_call.get("args") or {}
        cmd_text = (
            args.get("CommandLine")
            or args.get("command")
            or ""
        )
        path_text = (
            args.get("Path")
            or args.get("file_path")
            or args.get("path")
            or args.get("TargetFile")
            or args.get("SearchPath")
            or ""
        )
        target_text = f"{cmd_text} {path_text}".strip()
        if not target_text:
            return []

        excluded = set(exclude_ids or [])
        facts = self.list_facts()
        matched = []
        needs_save = False
        injected_delta = 0
        saved_delta = 0

        for f in facts:
            fid = f.get("id")
            if fid in excluded:
                continue
            pattern = f.get("pattern", "")
            if not pattern or pattern == "*":
                continue
            category = f.get("category", "general")
            try:
                if regex_matches(pattern, target_text):
                    # For CLI commands with non-rule/gotcha facts, only fire if prescribed flags/subcommands are missing.
                    # For file/path tools (Read/Edit/Write) or explicit 'rule'/'gotcha' facts, fire once per session!
                    if cmd_text and category not in ("rule", "gotcha"):
                        if only_missing_flags and not is_fact_missing_in_command(f.get("fact", ""), cmd_text):
                            continue
                    matched.append(f)
                    if record_hits:
                        tok_cost = estimate_tokens(f.get("fact", "")) + 4
                        tok_saved = f.get("est_savings", DEFAULT_SAVED_TOKENS_PER_HIT)
                        f["hits"] = f.get("hits", 0) + 1
                        f["injected_tokens"] = f.get("injected_tokens", 0) + tok_cost
                        f["saved_tokens"] = f.get("saved_tokens", 0) + tok_saved
                        f["last_hit_at"] = datetime.utcnow().isoformat() + "Z"
                        injected_delta += tok_cost
                        saved_delta += tok_saved
                        needs_save = True
            except re.error:
                continue

        if needs_save and record_hits:
            self._save_merged(facts)
            self.update_metrics(
                fact_hits=len(matched),
                tool_hint_hits=len(matched),
                injected_tokens=injected_delta,
                saved_discovery_tokens=saved_delta,
            )

        return matched

    def auto_learn_from_recovery(self, failed_cmd: str, succeeded_cmd: str) -> Optional[Dict]:
        """
        Automatically extract a micro-fact when a command fails and a subsequent command
        using the same CLI tool succeeds with different flags or runner prefix.
        """
        if not failed_cmd or not succeeded_cmd or failed_cmd.strip() == succeeded_cmd.strip():
            return None

        # Same tool in both commands, even behind wrappers ('cd x && rtk proxy pytest', 'python3 -m pytest')
        target_bin = find_common_bin(failed_cmd)
        if not target_bin or find_common_bin(succeeded_cmd) != target_bin:
            return None

        # Ignore if only target file path or working directory changed (not a CLI invocation recovery)
        if _extract_invocation_signature(failed_cmd, target_bin) == _extract_invocation_signature(succeeded_cmd, target_bin):
            return None

        # Avoid overwriting manual facts for this binary
        for existing in self.list_facts():
            if existing.get("pattern") == target_bin and existing.get("category") != "auto-learned":
                return None

        fact_text = redact_secrets(
            f"Use '{succeeded_cmd.strip()}' instead of '{failed_cmd.strip()}' when running {target_bin}."
        )
        entry = self.add_fact(
            pattern=target_bin,
            fact=fact_text,
            category="auto-learned",
            fact_id=f"auto_{target_bin}",
        )
        self.update_metrics(auto_learned_facts=1)
        return entry

    def auto_learn_from_correction(self, prompt: str) -> Optional[Dict]:
        """
        Automatically extract and store a domain/file 'gotcha' micro-fact when the user explicitly
        states a concise correction or rule with concrete technical targets (files, modules, CLI tools).
        """
        if not prompt or "?" in prompt:
            return None
        norm = normalize_text(prompt.strip().replace("\n", " "))
        # Only auto-persist concise single-rule directives (15..240 chars), never questions or long logs
        if len(norm) < 15 or len(norm) > 240 or _QUESTION_START_RE.search(norm):
            return None
        if not _CORRECTION_TRIGGER_RE.search(norm):
            return None

        targets = _extract_correction_targets(norm)
        if not targets:
            return None

        pattern = "|".join(targets[:2])
        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", targets[0])[:32]
        # Text digest keeps two different rules about the same file apart; the same prompt stays idempotent.
        fact_id = f"auto_gotcha_{slug}_{hashlib.sha1(norm.encode('utf-8')).hexdigest()[:6]}"

        # Avoid overwriting manual facts with the same pattern
        for existing in self.list_facts():
            if existing.get("pattern") == pattern and not str(existing.get("id", "")).startswith("auto_"):
                return None

        entry = self.add_fact(
            pattern=pattern,
            fact=redact_secrets(norm),
            category="gotcha",
            fact_id=fact_id,
        )
        self.update_metrics(auto_learned_facts=1)
        return entry

    def format_ephemeral_message(
        self,
        matched_facts: List[Dict],
        correction_hint: Optional[str] = None,
        reflect_mode: Optional[str] = None,
    ) -> Optional[str]:
        """Format matched facts and optional correction nudge into a single concise prompt injection block."""
        if not matched_facts and not correction_hint:
            return None

        lines: List[str] = []
        if matched_facts:
            lines.append("[CENTSIBLE MICRO-SKILLS: Active Repository Facts]")
            lines.append("Apply the following verified facts for this turn to avoid repetitive search/execution:")
            for f in matched_facts:
                lines.append(f"- {f.get('fact')}")
        if correction_hint:
            lines.append(correction_hint)
        if reflect_mode == "inline":
            lines.append("[Centsible Self-Learning] If you discover new repo quirks or command flags in this turn, save them via 'centsible facts add' before your final response.")

        return "\n".join(lines)

    def _scope_path(self, scope: str) -> Path:
        if scope == "global":
            if not self.global_path:
                raise ValueError("No global facts file configured for this engine.")
            return self.global_path
        return self.storage_path

    @staticmethod
    def _write(path: Path, facts: List[Dict]) -> None:
        # Hand-written `#` comments and unparsable lines are not facts, but must survive a rewrite.
        # shortcut: they move to the top of the file, original interleaving is not kept.
        kept: List[str] = []
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if not s:
                    continue
                if s.startswith("#"):
                    kept.append(s)
                    continue
                try:
                    json.loads(s)
                except json.JSONDecodeError:
                    kept.append(s)
        body = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in facts)
        atomic_write_text(path, "".join(k + "\n" for k in kept) + body)

    def _save_merged(self, facts: List[Dict]) -> None:
        """Persist a merged view (callers hold both locks): project facts to the project file, global ones
        back into the global file by id, so global entries overridden by a project fact are left alone."""
        strip = lambda f: {k: v for k, v in f.items() if k != "_scope"}
        self._write(self.storage_path, [strip(f) for f in facts if f.get("_scope") != "global"])
        updated = {f.get("id"): strip(f) for f in facts if f.get("_scope") == "global"}
        if updated and self.global_path:
            self._write(self.global_path, [updated.get(g.get("id"), g) for g in self._read(self.global_path)])
