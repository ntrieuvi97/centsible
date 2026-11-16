"""Fact audits: `verify` (do the files/commands a fact names still exist) and `replay` (how would each
fact's pattern have behaved on past Claude Code sessions). Both are read-only and make no claims about
token savings; replay reports firing behaviour only."""

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple

from core.fact_helpers import (
    extract_fact_prescriptions,
    is_fact_missing_in_command,
    regex_matches,
)

_EXT = r"(?:py|ts|tsx|js|jsx|rs|go|java|kt|rb|php|sql|prisma|json|yaml|yml|toml|sh|md|vue|svelte|cfg|ini|lock)"
_PATH_RE = re.compile(r"(?<![\w/.:~-])((?:\.{1,2}/)?[\w@.-]+(?:/[\w@.-]+)+/?|[\w@-][\w@.-]*\." + _EXT + r")(?![\w/-])")
_SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".tox", "target"}
_SHELL_WORDS = {"cd", "export", "source", "echo", "set", "unset", "alias", "sudo", "env"}
WALK_LIMIT = 200_000


def _walk_names(root: Path) -> Set[str]:
    """Every file basename under root (skipping vendored dirs), for bare-filename checks."""
    names: Set[str] = set()
    seen = 0
    for _, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        names.update(files)
        seen += len(files)
        if seen > WALK_LIMIT:  # shortcut: huge monorepos get a partial name index, raise if it bites
            break
    return names


def _referenced_paths(text: str) -> List[str]:
    out = []
    for m in _PATH_RE.finditer(text.replace("\\.", ".")):
        tok = m.group(1).rstrip("/")
        if "://" in tok or any(c in tok for c in "*$<>{}"):
            continue
        out.append(tok)
    return out


def _referenced_binaries(text: str) -> List[str]:
    """First word of every quoted/backticked command-looking segment in a fact."""
    bins = []
    for seg in re.findall(r"['\"`]([^'\"`]{2,80})['\"`]", re.split(r"\binstead of\b", text, flags=re.I)[0]):
        words = [w for w in seg.split() if not re.match(r"\w+=", w)]
        if words and re.match(r"^[a-z][\w.+-]*$", words[0]) and words[0] not in _SHELL_WORDS and len(words) > 1:
            bins.append(words[0])
    return bins


def verify_facts(facts: List[Dict[str, Any]], root: Path) -> List[Dict[str, str]]:
    """Issues as {id, level, message}. level 'stale' = a named path is gone (act on it);
    'warn' = a named binary is not on this machine's PATH (may just be this machine)."""
    issues: List[Dict[str, str]] = []
    names: Optional[Set[str]] = None
    for f in facts:
        fid = str(f.get("id"))
        seen: Set[str] = set()
        for tok in _referenced_paths(f"{f.get('fact', '')} {f.get('pattern', '')}"):
            if tok in seen:
                continue
            seen.add(tok)
            rel = tok[2:] if tok.startswith("./") else tok
            if "/" in rel:
                explicit = tok.startswith(("./", "../"))
                last_has_ext = "." in rel.rsplit("/", 1)[-1]
                if not (explicit or last_has_ext or (root / rel.split("/", 1)[0]).exists()):
                    continue  # "and/or", "client/server": prose, not a path
                if not (root / rel).exists():
                    issues.append({"id": fid, "level": "stale", "message": f"path not found: {tok}"})
            else:
                names = _walk_names(root) if names is None else names
                if rel not in names:
                    issues.append({"id": fid, "level": "stale", "message": f"file not found anywhere in repo: {tok}"})
        for b in dict.fromkeys(_referenced_binaries(str(f.get("fact", "")))):
            if not shutil.which(b):
                issues.append({"id": fid, "level": "warn", "message": f"command not on PATH here: {b}"})
    return issues


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def iter_session_events(transcript_dir: Path, days: Optional[int] = None) -> Iterator[Tuple[str, str, str]]:
    """(session, kind, text): kind 'prompt' for typed user turns, 'tool' for a Bash command / file path."""
    cutoff = time.time() - days * 86400 if days else 0
    for path in sorted(transcript_dir.glob("*.jsonl")):
        try:
            if path.stat().st_mtime < cutoff:
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                step = json.loads(line)
            except ValueError:
                continue
            if not isinstance(step, dict) or step.get("isSidechain"):
                continue
            msg = step.get("message") if isinstance(step.get("message"), dict) else {}
            content = msg.get("content")
            if step.get("type") == "user":
                text = _text_of(content).strip()
                if text and not text.startswith("<"):  # injected reminders / command wrappers are not typed turns
                    yield path.stem, "prompt", text
            elif step.get("type") == "assistant" and isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_use":
                        a = b.get("input") or {}
                        cmd, target = a.get("command") or "", a.get("file_path") or a.get("path") or ""
                        if cmd or target:
                            yield path.stem, "tool", f"{cmd} {target}".strip() if cmd else target


def replay_facts(facts: List[Dict[str, Any]], transcript_dir: Path, days: Optional[int] = None) -> Dict[str, Any]:
    """Run every fact's pattern over past prompts and tool calls. Per fact: how often it would fire and, for
    CLI facts that prescribe flags/commands, how often the command already complied (hint redundant)."""
    rows = {
        str(f.get("id")): {
            "id": str(f.get("id")), "pattern": f.get("pattern", ""), "prompt_fires": 0, "tool_fires": 0,
            "tool_needed": 0, "sessions": set(), "prescriptive": any(extract_fact_prescriptions(f.get("fact", ""))),
        }
        for f in facts if f.get("pattern") and f.get("pattern") != "*" and f.get("category") != "always"
    }
    by_id = {str(f.get("id")): f for f in facts}
    totals = {"prompts": 0, "tool_calls": 0, "sessions": set()}
    for session, kind, text in iter_session_events(transcript_dir, days):
        totals["sessions"].add(session)
        totals["prompts" if kind == "prompt" else "tool_calls"] += 1
        for fid, r in rows.items():
            try:
                if not regex_matches(r["pattern"], text):
                    continue
            except re.error:
                continue
            r["sessions"].add(session)
            if kind == "prompt":
                r["prompt_fires"] += 1
            else:
                r["tool_fires"] += 1
                if is_fact_missing_in_command(by_id[fid].get("fact", ""), text):
                    r["tool_needed"] += 1
    for r in rows.values():
        r["sessions"] = len(r["sessions"])
        r["verdict"] = _verdict(r, totals)
    totals["sessions"] = len(totals["sessions"])
    return {"totals": totals, "facts": list(rows.values())}


MIN_SAMPLE = 20


def _verdict(r: Dict[str, Any], totals: Dict[str, Any]) -> str:
    fires = r["prompt_fires"] + r["tool_fires"]
    if totals["prompts"] + totals["tool_calls"] < MIN_SAMPLE:
        return "not enough history"
    if fires == 0:
        return "never fires: loosen the pattern or remove the fact"
    if totals["prompts"] >= MIN_SAMPLE and r["prompt_fires"] / totals["prompts"] > 0.25:
        return "too broad: fires on over 25% of prompts, tighten the pattern"
    if r["prescriptive"] and r["tool_fires"] >= 5 and r["tool_needed"] == 0:
        return "redundant: every matching command already followed it"
    return "ok"
