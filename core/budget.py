"""Per-session spend budget: a one-time soft warning, then a hard stop on further tool calls.

Token usage is *measured* from the host transcript when the hook payload names one (Claude Code sends
`transcript_path`); other hosts fall back to an *estimate* of tool input/output size, which undercounts
because it ignores the context re-sent on every model call. The call budget is exact on every host.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

WARN_RATIO = 0.8
# Cached prompt reads are billed at a fraction of fresh input (about 10% on Anthropic), so counting them in
# full would make long sessions look several times more expensive than they are.
CACHE_READ_WEIGHT = 0.1
MAX_SEEN_MESSAGE_IDS = 200


def usage_tokens(usage: Dict[str, Any]) -> int:
    """Billable-equivalent tokens of one model response's `usage` block."""
    def n(key: str) -> int:
        try:
            return max(0, int(usage.get(key) or 0))
        except (TypeError, ValueError):
            return 0
    fresh = n("input_tokens") + n("cache_creation_input_tokens") + n("output_tokens")
    return fresh + int(n("cache_read_input_tokens") * CACHE_READ_WEIGHT)


def read_transcript_usage(path: Path, offset: int, seen_ids: List[str]) -> Tuple[int, int, List[str]]:
    """Tokens used by model responses appended to a JSONL transcript since byte `offset`.

    Returns (delta_tokens, new_offset, seen_ids). Only complete lines are consumed, so a line being written
    is read on the next call. A message streamed as several lines repeats one `usage` under the same
    message id; each id is counted once.
    """
    try:
        size = path.stat().st_size
    except OSError:
        return 0, offset, seen_ids
    if offset > size:  # transcript was rotated or rewritten
        offset = 0
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            chunk = f.read()
    except OSError:
        return 0, offset, seen_ids
    end = chunk.rfind(b"\n")
    if end < 0:
        return 0, offset, seen_ids
    seen = list(seen_ids)
    seen_set = set(seen)
    delta = 0
    for raw in chunk[: end + 1].splitlines():
        try:
            step = json.loads(raw)
        except ValueError:
            continue
        msg = step.get("message") if isinstance(step, dict) else None
        if not isinstance(msg, dict) or not isinstance(msg.get("usage"), dict):
            continue
        usage = msg["usage"]
        mid = str(msg.get("id") or step.get("uuid") or "")
        if mid:
            if mid in seen_set:
                continue
            seen_set.add(mid)
            seen.append(mid)
        delta += usage_tokens(usage)
    return delta, offset + end + 1, seen[-MAX_SEEN_MESSAGE_IDS:]


def evaluate_budget(
    used_tokens: int,
    tokens_measured: bool,
    used_calls: int,
    token_limit: int,
    call_limit: int,
) -> Tuple[Optional[str], Optional[str]]:
    """(level, message): level is 'stop' when a limit is reached, 'warn' past WARN_RATIO, else None."""
    checks = []
    if token_limit > 0:
        source = "measured from transcript" if tokens_measured else "estimated from tool I/O only"
        checks.append((used_tokens, token_limit, f"{used_tokens:,}/{token_limit:,} tokens ({source})", "session_budget_tokens"))
    if call_limit > 0:
        checks.append((used_calls, call_limit, f"{used_calls:,}/{call_limit:,} tool calls", "session_budget_calls"))
    over = [c for c in checks if c[0] >= c[1]]
    if over:
        used = "; ".join(c[2] for c in over)
        key = over[0][3]
        return "stop", (
            f"[Centsible Budget] Session budget reached: {used}. Tool calls are blocked for the rest of this session. "
            "Summarize what is done and what is left, then stop. "
            f"To continue, the user can run `centsible config set {key} <higher>` or `centsible config unset {key}`."
        )
    near = [c for c in checks if c[0] >= WARN_RATIO * c[1]]
    if near:
        used = "; ".join(c[2] for c in near)
        return "warn", (
            f"[Centsible Budget] {int(WARN_RATIO * 100)}% of the session budget is used: {used}. "
            "Prefer the cheapest path to done: avoid broad reads and re-running long commands."
        )
    return None, None
