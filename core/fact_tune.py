"""`facts tune`: evidence-based suggestions to remove, narrow or merge facts.

Evidence comes from three places: per-fact counters (hits vs. verified applications), the event log (which
sessions a fact was applied in), and past transcripts (what each pattern, and each `a|b|c` branch of it, would
have matched). A narrowing is only proposed when a branch fired in several sessions and the fact was never
applied in any of them while another branch did lead to an application. Nothing changes without `--apply`.
"""

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from core.fact_audit import MIN_SAMPLE, iter_session_events, replay_facts
from core.fact_helpers import compute_fact_utility, normalize_text, regex_matches

NOISY_MAX_APPLY_RATE = 0.05
MIN_BRANCH_SESSIONS = 3
OVERLAP_MIN_FIRES = 5
OVERLAP_JACCARD = 0.8


def split_alternation(pattern: str) -> List[str]:
    """Top-level `|` branches of a regex (ignoring `|` inside groups, classes or after a backslash)."""
    branches, depth, in_class, escaped, cur = [], 0, False, False, []
    for ch in pattern:
        if escaped:
            escaped = False
        elif ch == "\\":
            escaped = True
        elif in_class:
            in_class = ch != "]"
        elif ch == "[":
            in_class = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == "|" and depth == 0:
            branches.append("".join(cur))
            cur = []
            continue
        cur.append(ch)
    branches.append("".join(cur))
    return branches


def _matches(pattern: str, text: str) -> bool:
    try:
        return regex_matches(pattern, text)
    except re.error:
        return False


def _fire_sessions(pattern: str, events: List[Tuple[str, str, str]]) -> Tuple[int, Set[str]]:
    fires, sessions = 0, set()
    for session, _kind, text in events:
        if _matches(pattern, text):
            fires += 1
            sessions.add(session)
    return fires, sessions


def applied_sessions_by_fact(log_events: Iterable[Dict[str, Any]]) -> Dict[str, Set[str]]:
    out: Dict[str, Set[str]] = {}
    for e in log_events:
        if e.get("event") == "applied":
            for fid in e.get("fact_ids", []):
                out.setdefault(fid, set()).add(str(e.get("session", "")))
    return out


def _tunable(f: Dict[str, Any]) -> bool:
    return bool(f.get("pattern")) and f.get("pattern") != "*" and f.get("category") != "always"


def tune_facts(
    facts: List[Dict[str, Any]],
    transcript_dir: Optional[Path] = None,
    days: Optional[int] = 30,
    log_events: Optional[List[Dict[str, Any]]] = None,
    min_hits: int = 20,
) -> Dict[str, Any]:
    """Suggestions as {action: remove|narrow|merge, id, reason, ...}; `applicable` marks the ones --apply performs."""
    tdir = transcript_dir if transcript_dir is not None and transcript_dir.is_dir() else None
    history = list(iter_session_events(tdir, days)) if tdir else []
    enough = len(history) >= MIN_SAMPLE
    verdicts = {r["id"]: r["verdict"] for r in replay_facts(facts, tdir, days)["facts"]} if tdir and enough else {}
    applied_in = applied_sessions_by_fact(log_events or [])
    suggestions: List[Dict[str, Any]] = []
    fire_sets: Dict[str, Set[int]] = {}

    for f in facts:
        if not _tunable(f):
            continue
        fid, pattern = str(f.get("id")), str(f.get("pattern"))
        hits, applied = int(f.get("hits", 0)), int(f.get("attributed_hits", 0))
        verdict = verdicts.get(fid, "")
        if enough:
            fire_sets[fid] = {i for i, (_s, _k, text) in enumerate(history) if _matches(pattern, text)}

        if verdict.startswith("never fires") and hits == 0:
            suggestions.append(_s("remove", f, "never fired in past sessions and has 0 live hits", True))
            continue
        if verdict.startswith("redundant") and applied == 0:
            suggestions.append(_s("remove", f, "every matching command already followed it (hint is redundant)", True))
            continue

        noisy = hits >= min_hits and applied / max(hits, 1) < NOISY_MAX_APPLY_RATE
        if not (noisy or verdict.startswith("too broad")):
            continue
        why = f"shown {hits}x, applied {applied}x" if noisy else verdict
        narrowed = _narrow_by_branch(pattern, history, applied_in.get(fid, set())) if enough else None
        if narrowed:
            new_pattern, dropped, before, after = narrowed
            suggestions.append(dict(
                _s("narrow", f, f"{why}; branch(es) {', '.join(repr(d) for d in dropped)} fired in "
                                f"{MIN_BRANCH_SESSIONS}+ sessions where the fact was never applied", True),
                new_pattern=new_pattern, fires_before=before, fires_after=after,
            ))
        else:
            hint = "anchor it on an identifier, a path or a tool plus a symptom"
            if not enough:
                hint += " (no transcript history: pass --transcripts DIR for branch-level evidence)"
            suggestions.append(_s("narrow", f, f"{why}; {hint}", False))

    suggestions.extend(_merges(facts, fire_sets))
    return {"history_events": len(history), "suggestions": suggestions}


def _s(action: str, f: Dict[str, Any], reason: str, applicable: bool) -> Dict[str, Any]:
    return {"action": action, "id": str(f.get("id")), "pattern": f.get("pattern"), "fact": f.get("fact"),
            "scope": f.get("_scope", "project"), "reason": reason, "applicable": applicable}


def _narrow_by_branch(pattern: str, history: List[Tuple[str, str, str]], applied_sessions: Set[str]):
    """Drop branches that fired in >= MIN_BRANCH_SESSIONS sessions with no application, keeping at least one
    branch that did lead to an application. Returns (new_pattern, dropped, fires_before, fires_after) or None."""
    branches = [b for b in split_alternation(pattern) if b]
    if len(branches) < 2 or not applied_sessions:
        return None
    keep, dropped = [], []
    for b in branches:
        _fires, sessions = _fire_sessions(b, history)
        if len(sessions) >= MIN_BRANCH_SESSIONS and not sessions & applied_sessions:
            dropped.append(b)
        else:
            keep.append(b)
    if not dropped or not any(_fire_sessions(b, history)[1] & applied_sessions for b in keep):
        return None
    new_pattern = "|".join(keep)
    return new_pattern, dropped, _fire_sessions(pattern, history)[0], _fire_sessions(new_pattern, history)[0]


def _merges(facts: List[Dict[str, Any]], fire_sets: Dict[str, Set[int]]) -> List[Dict[str, Any]]:
    """Same fact text under two ids (auto-removable), or two patterns firing on nearly the same events (manual)."""
    out: List[Dict[str, Any]] = []
    tunable = [f for f in facts if _tunable(f)]
    done: Set[str] = set()
    for i, a in enumerate(tunable):
        for b in tunable[i + 1:]:
            ida, idb = str(a.get("id")), str(b.get("id"))
            if ida in done or idb in done:
                continue
            keep, drop = (a, b) if compute_fact_utility(a) >= compute_fact_utility(b) else (b, a)
            if normalize_text(str(a.get("fact", ""))).lower() == normalize_text(str(b.get("fact", ""))).lower():
                out.append(dict(_s("merge", drop, f"same fact text as [{keep.get('id')}], which has higher utility", True),
                                keep_id=str(keep.get("id"))))
                done.add(str(drop.get("id")))
                continue
            sa, sb = fire_sets.get(ida, set()), fire_sets.get(idb, set())
            if len(sa) >= OVERLAP_MIN_FIRES and len(sb) >= OVERLAP_MIN_FIRES:
                jac = len(sa & sb) / len(sa | sb)
                if jac >= OVERLAP_JACCARD:
                    out.append(dict(_s("merge", drop, f"fires on the same events as [{keep.get('id')}] "
                                                      f"({jac:.0%} overlap); combine the two lines into one fact", False),
                                    keep_id=str(keep.get("id"))))
    return out


def apply_suggestions(engine, suggestions: List[Dict[str, Any]]) -> List[str]:
    """Perform the applicable suggestions; returns one log line per change."""
    done = []
    for s in suggestions:
        if not s.get("applicable"):
            continue
        scope = "global" if s.get("scope") == "global" else "project"
        if s["action"] in ("remove", "merge"):
            if engine.remove_fact(s["id"], scope=scope):
                done.append(f"removed [{s['id']}]")
        elif s["action"] == "narrow" and s.get("new_pattern"):
            f = engine.get_fact(s["id"]) or {}
            engine.add_fact(
                pattern=s["new_pattern"], fact=f.get("fact", s.get("fact", "")), category=f.get("category", "general"),
                fact_id=s["id"], scope=scope, force=True, tool=f.get("tool"), arg_keys=f.get("arg_keys"),
            )
            done.append(f"narrowed [{s['id']}]: {s['pattern']} -> {s['new_pattern']}")
    return done
