#!/usr/bin/env python3
"""
Pure helpers for the facts engine: linting, pattern matching, shell parsing, attribution, summaries.
No FactsEngine here; core.facts_engine re-exports every name below.
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
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_SAVED_TOKENS_PER_HIT = 1500
DEFAULT_SAVED_TOKENS_PER_LOOP_STOP = 2000
DEFAULT_USD_PER_MTOK = 3.0
TOOL_CATEGORIES = frozenset({"cli", "tool", "syntax", "runner"})
DEFAULT_MAX_PROMPT_FACTS = 3

MAX_FACT_CHARS = 240
MIN_BARE_TERM_LEN = 8
SAVED_WILDCARD_SPAN = 40
LINT_EXEMPT_CATEGORIES = frozenset({"always"})
# Words that appear in nearly every prompt of a dev repo; a pattern made only of these fires everywhere.
GENERIC_TERMS = frozenset({
    "run", "runs", "running", "test", "tests", "testing", "build", "create", "make", "fix", "check",
    "file", "files", "error", "errors", "fail", "failed", "order", "orders", "code", "data", "call",
    "use", "add", "get", "set", "new", "issue", "bug", "bot", "script", "command", "don", "chạy", "tạo",
    "lên", "lỗi", "xem", "sửa", "kiểm", "tra", "đơn",
})
_IDENT_RE = re.compile(r"[_./:\-]|\d|[a-z][A-Z]")
_WORD_RE = re.compile(r"[^\W\d_]+")

STANDARD_TRIVIAL_COMMANDS = frozenset({
    "npm test", "npm run test", "npm start", "npm run dev", "npm run build",
    "pnpm test", "pnpm run test", "pnpm start", "pnpm dev", "pnpm build",
    "yarn test", "yarn start", "yarn dev", "yarn build",
    "bun test", "bun run dev", "bun run build",
    "git status", "git pull", "git commit", "git add", "git push", "git checkout",
    "python main.py", "python app.py", "python index.py",
    "pytest", "pytest tests", "pytest tests/",
    "cargo build", "cargo test", "cargo run",
    "go run .", "go test ./...", "go build",
    "mvn test", "mvn clean install",
})

_BARE_CMD_RE = re.compile(
    r"^(npm|pnpm|yarn|bun|git|cargo|go|python|python3|mvn)\s+([a-zA-Z0-9_.-]+)$",
    re.IGNORECASE,
)

_GENERIC_ADVICE_PHRASES = frozenset({
    "clean code", "write clean code", "write tests", "test your code",
    "test before commit", "be careful", "check errors", "check error",
    "format code", "viết test", "kiểm tra code", "cẩn thận",
})


def lint_fact_content(fact: str) -> Optional[str]:
    """
    Validate that a micro-fact is high-signal, non-trivial, and actionable.
    Rejects standard commands known to any LLM and generic platitudes.
    """
    norm = fact.strip().lower()
    if not norm:
        return "Fact content cannot be empty."

    # Strip conversational/directive prefixes (e.g. "Run npm test", "Use 'git status'")
    cleaned = re.sub(
        r"^(always\s+|remember\s+to\s+|please\s+|run\s+with\s+|run\s+|use\s+|chạy\s+|hãy\s+|nhớ\s+)",
        "",
        norm,
    ).strip(" '\"`.,;:")

    # 1. Exact match against known trivial commands
    if cleaned in STANDARD_TRIVIAL_COMMANDS:
        return f"Command '{cleaned}' is a standard trivial command known by any LLM. Zero-waste rule rejects this."

    # 2. Bare tool + single argument commands without flags or paths (e.g. "git diff", "cargo check", "npm start")
    m_bare = _BARE_CMD_RE.match(cleaned)
    if m_bare:
        subcmd = m_bare.group(2).lower()
        if subcmd in ("diff", "log", "start", "check", "build", "test", "run", "status", "push", "pull"):
            return f"Command '{cleaned}' is a standard bare CLI invocation without specialized flags or arguments."

    # 3. Generic platitudes / empty advice without actionable anchors
    for phrase in _GENERIC_ADVICE_PHRASES:
        if phrase in norm and not any(ch in norm for ch in ("--", "-", "=", "/", ".")):
            return f"Fact contains generic advice ('{phrase}') without actionable flags or path anchors."

    return None


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


def _split_top_level_alternatives(pattern: str) -> List[str]:
    """Split `a|b.{0,9}(c|d)` on `|` outside parens/classes, unwrapping one enclosing `( ... )`."""
    pattern = pattern.strip()
    if pattern.startswith("(") and pattern.endswith(")"):
        depth = 0
        for i, c in enumerate(pattern):
            depth += (c == "(") - (c == ")")
            if depth == 0 and i < len(pattern) - 1:
                break
        else:
            pattern = pattern[1:-1]
    parts, buf, depth, in_class, i = [], [], 0, False, 0
    while i < len(pattern):
        c = pattern[i]
        if c == "\\" and i + 1 < len(pattern):
            buf.append(pattern[i:i + 2])
            i += 2
            continue
        if in_class:
            in_class = c != "]"
        elif c == "[":
            in_class = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == "|" and depth == 0:
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(c)
        i += 1
    parts.append("".join(buf))
    return [p for p in parts if p.strip()]


def lint_pattern(pattern: str) -> Optional[str]:
    """Return why `pattern` would fire on unrelated prompts, or None when it is specific enough.

    Every top-level alternative needs an anchor: an identifier-like token (`deploy_staging`, `run-e2e`,
    `user_id`, `a/b`), a long unique word, or a combination of several non-generic words
    (`pytest.{0,40}(spawn|fail)`: a tool plus a symptom). A lone short word (`pip`, `rtk`, `helm`) or only generic words
    (`run.*test`) is rejected, because it matches questions and unrelated work.
    """
    if pattern == "*":
        return None
    for alt in _split_top_level_alternatives(pattern):
        words = [w.lower() for w in _WORD_RE.findall(re.sub(r"\\[a-zA-Z]", " ", alt))]  # \b \s \d are not words
        literal = re.sub(r"\.(\{\d*,?\d*\}|[*+?])|\(\?[:=!]", "", alt)  # drop wildcards, keep literal . _ / - :
        if not words or _IDENT_RE.search(literal):
            continue
        specific = {w for w in words if w not in GENERIC_TERMS}
        if not specific:
            return f"alternative '{alt}' only has generic words ({', '.join(sorted(set(words)))})"
        if _PLAIN_TERM_RE.fullmatch(alt):
            if len(words) == 1 and len(words[0]) < MIN_BARE_TERM_LEN:
                return f"alternative '{alt}' is a lone short word; add context or an identifier"
        elif len(set(words)) < 2 and len(words[0]) < MIN_BARE_TERM_LEN:
            return f"alternative '{alt}' reduces to one short word; add context or an identifier"
    return None


# Words in a correction that say nothing about the situation (directives, pronouns, politeness).
_CONTEXT_FILLER = frozenset({
    "never", "always", "dont", "must", "should", "please", "instead", "with", "that", "this", "then",
    "your", "when", "from", "into", "also", "only", "just", "does", "will", "here", "there",
    "đừng", "luôn", "phải", "không", "dùng", "chạy", "nhớ", "quy", "tắc", "lưu", "được", "nhưng", "và", "của",
})
_CONTEXT_WORD_RE = re.compile(r"[^\W\d_]{4,}")
CONTEXT_SPAN = 30


def contextualize_pattern(targets: List[str], text: str) -> Optional[str]:
    """Build a trigger from `targets`, pairing each loose one with situation words from `text`.

    A correction like "never use pip directly" yields target `pip`, which alone fires on every pip
    mention. Pairing it with "directly" (`pip.{0,30}(directly)`, either order) keeps the fact for the
    situation the user actually corrected. Targets that already anchor on an identifier stay as-is, and
    only the first loose target is kept (in `never use pip, use uv` the subject is pip, not uv).
    Returns None when no usable pattern remains (the caller then drops the fact).
    """
    parts: List[str] = []
    for target in targets:
        esc = re.escape(target)
        if lint_pattern(esc) is None:
            parts.append(esc)
            continue
        if any("{0," in p for p in parts):  # one contextualized target is the subject; later ones (e.g. the replacement tool) only add noise
            continue
        low = target.lower()
        context: List[str] = []
        for w in _CONTEXT_WORD_RE.findall(text.lower()):
            if w != low and w not in GENERIC_TERMS and w not in _CONTEXT_FILLER and w not in context:
                context.append(w)
        if not context:
            continue
        group = "|".join(re.escape(w) for w in context[:3])
        parts.append(f"({esc}.{{0,{CONTEXT_SPAN}}}({group})|({group}).{{0,{CONTEXT_SPAN}}}{esc})")
    if not parts:
        return None
    pattern = "|".join(parts)
    return pattern if lint_pattern(pattern) is None else None


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


def extract_fact_prescriptions(fact_text: str) -> Tuple[List[str], List[str]]:
    """
    Extract prescribed CLI flags and quoted values/identifiers from fact text,
    ignoring negative clauses ('instead of...').
    """
    if not fact_text:
        return [], []
    prescribed_text = re.split(r"\binstead of\b", fact_text, flags=re.IGNORECASE)[0]
    raw_flags = re.findall(r"(?:^|\s)(--[a-zA-Z0-9_-]+|-[a-zA-Z0-9])\b", prescribed_text)
    flags = [fl.strip() for fl in raw_flags if fl.strip()]
    quoted = [
        q.strip() for q in re.findall(r"['\"`]([^'\"`]{2,60})['\"`]", prescribed_text)
        if len(q.strip()) >= 2
    ]
    return flags, quoted


def is_fact_missing_in_command(fact_text: str, cmd_text: str) -> bool:
    """
    Check if a micro-fact prescribes specific CLI flags (e.g. --compact, -q)
    or ASCII CLI subcommands in quotes that are missing from cmd_text.
    """
    if not fact_text or not cmd_text:
        return False
    flags, quoted = extract_fact_prescriptions(fact_text)
    if flags:
        return any(fl not in cmd_text for fl in flags)
    ascii_quoted = [q for q in quoted if q.isascii()]
    if ascii_quoted:
        return any(q not in cmd_text for q in ascii_quoted)
    return False


def is_prescribed_command_present(fact_text: str, cmd_text: str) -> bool:
    """True when the fact quotes the exact command to run and `cmd_text` already contains all of it.

    Gotcha facts fire once per session even for CLI calls, so without this a command that already follows
    the fact (`rtk proxy python3 -m pytest tests -q`) still gets the hint and is counted as a saving.
    Only the longest quoted command-like segment counts; facts with none keep firing as before.
    """
    if not fact_text or not cmd_text:
        return False
    _, quoted = extract_fact_prescriptions(fact_text)
    candidates = [q for q in quoted if q.isascii() and len(q.split()) >= 2 and find_common_bin(q)]
    if not candidates:
        return False
    return all(tok in cmd_text for tok in max(candidates, key=len).split())


def check_fact_attribution(
    fact: Dict[str, Any],
    tool_call: Dict[str, Any],
    is_success: bool = True,
) -> bool:
    """
    Check whether a tool execution can be attributed to an injected micro-fact.
    Returns True if:
    1. A prescribed CLI flag from the fact is present in tool arguments.
    2. A quoted literal/identifier from the fact is present in tool arguments.
    3. The tool call target matches the fact's pattern and executed without error.
    """
    fact_text = fact.get("fact", "")
    flags, quoted = extract_fact_prescriptions(fact_text)

    args = tool_call.get("args") or {}
    arg_parts: List[str] = []
    for k, v in args.items():
        if isinstance(v, (str, int, float, bool)):
            arg_parts.append(str(v))
        elif isinstance(v, (list, dict)):
            arg_parts.append(json.dumps(v, ensure_ascii=False))
    combined_args = " ".join(arg_parts)

    if not combined_args:
        return False

    # 1. Flag attribution: check if prescribed flag is used
    if flags and any(fl in combined_args for fl in flags):
        return True

    # 2. Quoted literal attribution: check if quoted identifier/value is used
    if quoted:
        for q in quoted:
            if q.lower() in ("true", "false", "null", "none"):
                continue
            if q in combined_args or fold_vietnamese(q).lower() in fold_vietnamese(combined_args).lower():
                return True

    # 3. Domain pattern match + execution success
    pattern = fact.get("pattern", "")
    if pattern and pattern != "*" and is_success:
        tool_name = str(tool_call.get("name") or "")
        target_text = f"{tool_name} {combined_args}"
        if regex_matches(pattern, target_text):
            return True

    # 4. Technical identifier match from fact directive body (e.g. class names, module files, snake_case symbols)
    if is_success and fact_text:
        tech_targets = _extract_correction_targets(fact_text)
        for tok in tech_targets:
            if len(tok) >= 4 and tok.lower() not in COMMON_BINS and tok in combined_args:
                return True

    return False


DEAD_FACT_MIN_HITS = 20

_NOISY_RECOVERY_RE = re.compile(
    r"(?:<<['\"]?[A-Za-z0-9_]+|\b(?:python3?|node|ruby)\s+-(?:c|e)\b|\|\s*(?:head|tail|wc)\b|\bls\s+-[a-zA-Z]*[la])"
)


def is_noisy_recovery_command(cmd: str) -> bool:
    """Return True if a command is a one-off script, heredoc, or multi-step exploratory chain unsuitable for auto-learning."""
    if not cmd:
        return True
    s = cmd.strip()
    if len(s) > 140 or "\n" in s or ";" in s:
        return True
    return bool(_NOISY_RECOVERY_RE.search(s))


def compute_fact_utility(fact: Dict[str, Any]) -> float:
    """Compute Laplace-smoothed Bayesian attribution utility in (0.0, 1.0]."""
    hits = max(0, int(fact.get("hits", 0)))
    applied = max(0, int(fact.get("attributed_hits", 0)))
    return (applied + 1.0) / (hits + 2.0)


def is_dead_fact(fact: Dict[str, Any], min_hits: int = DEAD_FACT_MIN_HITS) -> bool:
    """Return True when a non-always fact has been shown >= min_hits times with 0 attributed applications."""
    if fact.get("category") == "always" or fact.get("pattern") == "*":
        return False
    hits = int(fact.get("hits", 0))
    applied = int(fact.get("attributed_hits", 0))
    return hits >= min_hits and applied == 0


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
    saved_loop = base.get("saved_loop_tokens", 0)
    saved_discovery = base.get("saved_discovery_tokens", 0)
    injected = base.get("injected_tokens", 0)
    gross_saved = saved_discovery + saved_loop
    net_saved = gross_saved - injected
    saved_usd = (net_saved / 1_000_000.0) * usd_per_mtok
    total_calls = base.get("tool_calls_total", 0)
    first_try_rate = (base.get("first_try_successes", 0) / total_calls * 100.0) if total_calls > 0 else 100.0
    error_rate = (base.get("tool_calls_failed", 0) / total_calls * 100.0) if total_calls > 0 else 0.0
    roi = (gross_saved / injected) if injected > 0 else 0.0

    cmd_calls = base.get("command_calls_total", 0)
    cmd_first_try_rate = (base.get("first_try_cmd_successes", 0) / cmd_calls * 100.0) if cmd_calls > 0 else 100.0
    cmd_error_rate = (base.get("command_calls_failed", 0) / cmd_calls * 100.0) if cmd_calls > 0 else 0.0

    fact_hits = base.get("fact_hits", 0)
    attr_hits = base.get("attributed_hits", 0)
    attribution_rate = round((attr_hits / fact_hits * 100.0), 1) if fact_hits > 0 else 0.0
    attributed_discovery = attr_hits * DEFAULT_SAVED_TOKENS_PER_HIT
    attributed_net_saved = attributed_discovery + saved_loop - injected

    return {
        **base,
        "gross_saved_tokens": gross_saved,
        "net_saved_tokens": net_saved,
        "attributed_hits": attr_hits,
        "attribution_rate": attribution_rate,
        "attributed_saved_tokens": attributed_discovery,
        "attributed_net_saved_tokens": attributed_net_saved,
        "command_calls_total": cmd_calls,
        "command_calls_failed": base.get("command_calls_failed", 0),
        "first_try_cmd_successes": base.get("first_try_cmd_successes", 0),
        "first_try_cmd_rate": round(cmd_first_try_rate, 1),
        "command_error_rate": round(cmd_error_rate, 1),
        "measured_loop_tokens": base.get("measured_loop_tokens", 0),
        "saved_usd": round(saved_usd, 4),
        "saved_cents": round(saved_usd * 100.0, 2),
        "first_try_success_rate": round(first_try_rate, 1),
        "tool_error_rate": round(error_rate, 1),
        "roi_multiplier": round(roi, 1),
        "usd_per_mtok": usd_per_mtok,
    }


def _build_recovery_pattern(target_bin: str, failed_cmd: str, succeeded_cmd: str) -> Optional[str]:
    """
    Construct a contextualized regex pattern for auto-learned recoveries so bare binary
    names like 'pytest' or 'npm' never fire on unrelated user prompts.
    """
    if lint_pattern(target_bin) is None:
        return target_bin

    shell_builtins = frozenset({"cd", "set", "export", "echo", "sudo", "time", "exec", "and", "then", "run"})
    target_low = target_bin.lower()

    # Priority 1: Context words/subcommands in failed_cmd (to match the prompt situation that failed)
    tokens_failed = [
        t.lstrip("-").lower() for t in re.findall(r"[a-zA-Z0-9_:-]{2,}", failed_cmd)
        if t.lstrip("-").lower() not in shell_builtins and t.lstrip("-").lower() != target_low
    ]
    # Priority 2: Distinct flags/subcommands in succeeded_cmd
    tokens_succeeded = [
        t.lstrip("-").lower() for t in re.findall(r"[a-zA-Z0-9_:-]{2,}", succeeded_cmd)
        if t.lstrip("-").lower() not in shell_builtins and t.lstrip("-").lower() != target_low and t.lstrip("-").lower() not in tokens_failed
    ]

    for tok in (tokens_failed + tokens_succeeded):
        if len(tok) < 2:
            continue
        pat = f"({target_bin}.{{0,40}}({re.escape(tok)})|({re.escape(tok)}).{{0,40}}{target_bin})"
        if lint_pattern(pat) is None:
            return pat

    return None
