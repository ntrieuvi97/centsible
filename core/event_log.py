"""Append-only JSONL log of hint / block / apply events, so effectiveness can be measured per agent and session."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.facts_engine import debug_warn, file_lock

EVENT_LOG_MAX_BYTES = 5 * 1024 * 1024


def get_event_log_path() -> Path:
    """~/.centsible/events.jsonl, or $CENTSIBLE_HOME/events.jsonl."""
    env_home = os.environ.get("CENTSIBLE_HOME")
    return (Path(env_home).expanduser().resolve() if env_home else Path.home() / ".centsible") / "events.jsonl"


def agent_from_conversation(conversation_id: Optional[str]) -> str:
    """OpenClaw session keys look like 'agent:<id>:...'; any other id has no agent."""
    parts = (conversation_id or "").split(":")
    return parts[1] if len(parts) > 2 and parts[0] == "agent" and parts[1] else ""


def log_event(event: str, conversation_id: Optional[str] = None, **fields: Any) -> None:
    """Append one event line. Never raises: a hook must not break the agent."""
    try:
        path = get_event_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        record: Dict[str, Any] = {
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "event": event,
            "session": conversation_id or "default",
            "agent": agent_from_conversation(conversation_id),
            "project": Path.cwd().resolve().name,  # lets `events` match the per-project `stats`
        }
        record.update({k: v for k, v in fields.items() if v not in (None, "", [])})
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with file_lock(path):
            if path.exists() and path.stat().st_size > EVENT_LOG_MAX_BYTES:
                path.replace(path.with_name(path.name + ".1"))
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
    except Exception as e:
        debug_warn("log_event", e)


def read_events(since: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Read the current and rotated log; malformed lines are skipped."""
    path = get_event_log_path()
    out: List[Dict[str, Any]] = []
    for p in (path.with_name(path.name + ".1"), path):
        if not p.exists():
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if since and rec.get("ts", "") < since.isoformat() + "Z":
                        continue
                    out.append(rec)
        except OSError as e:
            debug_warn("read_events", e)
    return out


def summarize_events(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Counts per agent and, per fact, how often it was shown vs. confirmed applied."""
    agents: Dict[str, Dict[str, int]] = {}
    facts: Dict[str, Dict[str, int]] = {}
    for e in events:
        kind = e.get("event", "")
        a = agents.setdefault(e.get("agent") or "(no agent)", {})
        a[kind] = a.get(kind, 0) + 1
        for fid in e.get("fact_ids", []):
            f = facts.setdefault(fid, {"shown": 0, "applied": 0})
            if kind in ("prompt_inject", "hint"):
                f["shown"] += 1
            elif kind == "applied":
                f["applied"] += 1
    return {"agents": agents, "facts": facts, "total": len(events)}
