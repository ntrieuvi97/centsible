"""Transcript analysis and before/after baseline comparison."""

import json
import re
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Ensure repo root is in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.facts_engine import estimate_tokens
from core.loop_guard import MUTATING_TOOLS, extract_error_from_payload


def _blocks(step: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Content blocks of a Claude Code message (tool_use / tool_result / text), else []."""
    msg = step.get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def analyze_transcript(transcript_path: Path) -> Dict[str, Any]:
    """
    Analyze an Antigravity or Claude Code JSONL session transcript to measure
    tool error rates, retry streaks, discovery steps, and token efficiency.
    Claude Code format: tool_use blocks sit in assistant messages, their tool_result
    (is_error, output) in the following user message; subagent (sidechain) steps are skipped.
    """
    stats: Dict[str, Any] = {
        "path": str(transcript_path),
        "started_at": None,
        "total_steps": 0,
        "user_turns": 0,
        "tool_calls": 0,
        "command_calls": 0,
        "failed_calls": 0,
        "retry_streaks": 0,
        "wasted_retry_calls": 0,
        "wasted_retry_tokens": 0,
        "discovery_steps_to_first_cmd": 0,
        "calls_before_first_edit": 0,
        "tokens_before_first_edit": 0,
        "edit_calls": 0,
        "first_try_cmd_successes": 0,
        "first_try_cmd_rate": 100.0,
    }
    if not transcript_path.exists():
        return stats

    steps: List[Dict[str, Any]] = []
    with open(transcript_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                steps.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    # tool_use_id -> (is_error, estimated tokens of its output)
    results: Dict[str, Tuple[bool, int]] = {}
    for step in steps:
        for b in _blocks(step):
            if b.get("type") == "tool_result":
                results[b.get("tool_use_id")] = (bool(b.get("is_error")), estimate_tokens(_result_text(b.get("content"))))

    saw_first_cmd = False
    saw_first_edit = False
    discovery_count = 0
    consecutive_errors = 0

    for step in steps:
        if not isinstance(step, dict) or step.get("isSidechain"):
            continue
        if not stats["started_at"] and isinstance(step.get("timestamp"), str):
            stats["started_at"] = step["timestamp"]
        stats["total_steps"] += 1
        blocks = _blocks(step)
        step_type = step.get("type") or step.get("role") or ""
        carries_results_only = bool(blocks) and all(b.get("type") == "tool_result" for b in blocks)
        if step_type in ("USER_INPUT", "user") and not carries_results_only:
            stats["user_turns"] += 1

        tool_calls = step.get("tool_calls") or []
        if not tool_calls and step.get("tool_name"):
            tool_calls = [{"name": step.get("tool_name"), "args": step.get("tool_input", {})}]
        if not tool_calls:
            tool_calls = [{"name": b.get("name"), "id": b.get("id")} for b in blocks if b.get("type") == "tool_use"]

        step_failed = (
            step.get("status") == "ERROR"
            or extract_error_from_payload(step) is not None
            or ("Command exited with code " in str(step.get("content", ""))
                and "Command exited with code 0" not in str(step.get("content", "")))
            or any(results.get(tc.get("id"), (False, 0))[0] for tc in tool_calls)
        )

        for tc in tool_calls:
            stats["tool_calls"] += 1
            t_name = str(tc.get("name") or tc.get("tool") or "")
            is_cmd = t_name in ("run_command", "Bash", "bash", "execute_command", "exec")
            if not saw_first_cmd:
                if is_cmd:
                    saw_first_cmd = True
                    stats["discovery_steps_to_first_cmd"] = discovery_count
                else:
                    discovery_count += 1
            if t_name in MUTATING_TOOLS:
                stats["edit_calls"] += 1
                saw_first_edit = True
            elif not saw_first_edit:
                # Exploration cost: every call (Read/Grep/Bash/...) and its output before the first edit
                stats["calls_before_first_edit"] += 1
                stats["tokens_before_first_edit"] += results.get(tc.get("id"), (False, 0))[1]
            if is_cmd:
                stats["command_calls"] += 1
                if not step_failed and consecutive_errors == 0:
                    stats["first_try_cmd_successes"] += 1

        if tool_calls:
            if step_failed:
                stats["failed_calls"] += 1
                consecutive_errors += 1
                if consecutive_errors == 2:
                    stats["retry_streaks"] += 1
                if consecutive_errors >= 2:
                    stats["wasted_retry_calls"] += 1
                    err_tokens = sum(results.get(tc.get("id"), (False, 0))[1] for tc in tool_calls)
                    stats["wasted_retry_tokens"] += err_tokens
            else:
                consecutive_errors = 0

    if stats["command_calls"] > 0:
        stats["first_try_cmd_rate"] = round(
            stats["first_try_cmd_successes"] / stats["command_calls"] * 100.0, 1
        )
    return stats


MIN_SESSIONS_PER_GROUP = 5


def claude_project_dir(cwd: Path) -> Path:
    """Claude Code stores a repo's transcripts in ~/.claude/projects/<cwd with non-alphanumerics as '-'>."""
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(cwd))


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    """ISO date/datetime (with or without Z) -> aware UTC datetime; None if unparseable."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def compare_baseline(transcript_dir: Path, since: datetime, min_sessions: int = MIN_SESSIONS_PER_GROUP) -> Dict[str, Any]:
    """
    Measure exploration cost (tool calls + output tokens before the first edit) in sessions
    started before vs after `since`. Sessions without any edit are skipped (pure Q&A has no baseline).
    Reports medians per group; savings are only reported when both groups have >= min_sessions.
    """
    groups: Dict[str, List[Dict[str, Any]]] = {"before": [], "after": []}
    for path in sorted(transcript_dir.glob("*.jsonl")):
        r = analyze_transcript(path)
        started = _parse_ts(r["started_at"]) or datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        if r["edit_calls"] > 0:
            groups["before" if started < since else "after"].append(r)

    out: Dict[str, Any] = {"since": since.isoformat(), "min_sessions": min_sessions}
    for name, rows in groups.items():
        out[name] = {
            "sessions": len(rows),
            "median_calls": statistics.median(r["calls_before_first_edit"] for r in rows) if rows else None,
            "median_tokens": statistics.median(r["tokens_before_first_edit"] for r in rows) if rows else None,
        }
    out["enough_data"] = all(out[g]["sessions"] >= min_sessions for g in groups)
    if out["enough_data"]:
        out["saved_calls_per_session"] = out["before"]["median_calls"] - out["after"]["median_calls"]
        out["saved_tokens_per_session"] = out["before"]["median_tokens"] - out["after"]["median_tokens"]
    return out
