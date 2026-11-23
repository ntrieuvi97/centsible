"""Zero-dependency MCP Apps (SEP-1865) stdio server for Centsible live dashboard."""

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import __version__
from core.event_log import read_events
from core.facts_engine import DEFAULT_USD_PER_MTOK, debug_warn, get_default_facts_path
from core.loop_guard import resolve_inject_mode, resolve_mode, resolve_reflect, resolve_threshold

APP_URI = "ui://centsible/dashboard.html"
APP_MIME = "text/html;profile=mcp-app"
DEFAULT_PROTOCOL_VERSION = "2025-03-26"


def _load_dashboard_html() -> str:
    html_path = Path(__file__).resolve().parent / "dashboard.html"
    return html_path.read_text(encoding="utf-8")


def _run_centsible_json(subargs: List[str], cwd: Path) -> Dict[str, Any]:
    """Invoke the centsible CLI with --json and return parsed JSON output."""
    cmd = [sys.executable, str(REPO_ROOT / "core" / "cli.py")] + subargs
    res = subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
    )
    if res.returncode != 0:
        err_msg = (res.stderr or res.stdout or f"exit {res.returncode}").strip()
        raise RuntimeError(f"centsible {' '.join(subargs)} failed: {err_msg}")
    return json.loads(res.stdout)


def _status_config() -> Dict[str, Any]:
    return {
        "active": True,
        "mode": resolve_mode(),
        "reflect": resolve_reflect(),
        "inject_mode": resolve_inject_mode(),
        "max_identical_calls": resolve_threshold("max_identical_calls"),
        "max_consecutive_errors": resolve_threshold("max_consecutive_errors"),
        "session_budget_tokens": resolve_threshold("session_budget_tokens"),
        "session_budget_calls": resolve_threshold("session_budget_calls"),
    }


def _recent_events(days: int, project_name: str, scope: str, limit: int = 15) -> List[Dict[str, Any]]:
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    evs = read_events(since=since)
    if scope != "all" and project_name:
        evs = [e for e in evs if e.get("project") == project_name]
    trimmed: List[Dict[str, Any]] = []
    for rec in reversed(evs[-limit:]):
        trimmed.append({
            "ts": rec.get("ts", ""),
            "event": rec.get("event", ""),
            "project": rec.get("project", ""),
            "agent": rec.get("agent", ""),
            "fact_ids": rec.get("fact_ids", []),
            "tool": rec.get("tool", ""),
        })
    return trimmed


def collect_dashboard_payload(arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Gather live metrics from `centsible stats --json`, `events --json`, and config."""
    args = arguments or {}
    raw_scope = args.get("scope")
    explicit_scope = raw_scope in ("project", "all")
    scope = raw_scope if explicit_scope else "project"

    raw_dir = args.get("project_dir")
    target_dir = Path(raw_dir).expanduser().resolve() if isinstance(raw_dir, str) and raw_dir.strip() else Path.cwd().resolve()
    if not target_dir.is_dir():
        target_dir = Path.cwd().resolve()

    try:
        days = max(1, int(args.get("days", 7)))
    except (TypeError, ValueError):
        days = 7
    try:
        rate = max(0.0, float(args.get("rate", DEFAULT_USD_PER_MTOK)))
    except (TypeError, ValueError):
        rate = DEFAULT_USD_PER_MTOK

    stats_args = ["stats", "--json", "--no-baseline", "--rate", str(rate)]
    if scope == "all":
        stats_args.append("--all")
    stats_data = _run_centsible_json(stats_args, cwd=target_dir)
    metrics = stats_data.get("metrics", {})

    # When opened from a non-repo cwd (e.g. sidebar global view) without explicit scope,
    # fall back to aggregated metrics across all projects if the current dir has no data.
    if not explicit_scope and not metrics.get("total_facts") and not metrics.get("tool_calls_total"):
        all_stats = _run_centsible_json(
            ["stats", "--json", "--no-baseline", "--rate", str(rate), "--all"],
            cwd=target_dir,
        )
        all_metrics = all_stats.get("metrics", {})
        if all_metrics.get("projects", 0) > 0 or all_metrics.get("tool_calls_total", 0) > 0 or all_metrics.get("total_facts", 0) > 0:
            scope = "all"
            metrics = all_metrics

    events_args = ["events", "--json", "--days", str(days)]
    if scope == "all":
        events_args.append("--all")
    events_data = _run_centsible_json(events_args, cwd=target_dir)
    events_data["recent"] = _recent_events(days=days, project_name=target_dir.name, scope=scope)

    if scope == "all":
        storage_path = f"all projects ({metrics.get('projects', 0)})"
    else:
        storage_path = str(get_default_facts_path([str(target_dir)]))

    return {
        "scope": scope,
        "project_name": target_dir.name,
        "project_dir": str(target_dir),
        "storage_path": storage_path,
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "metrics": metrics,
        "status": _status_config(),
        "events": events_data,
    }


def _tool_definitions() -> List[Dict[str, Any]]:
    common_props = {
        "scope": {
            "type": "string",
            "enum": ["project", "all"],
            "description": "Show metrics for the current project or aggregated across all projects.",
        },
        "project_dir": {
            "type": "string",
            "description": "Optional repository directory path (defaults to current working directory).",
        },
        "days": {
            "type": "integer",
            "minimum": 1,
            "default": 7,
            "description": "Event log lookback window in days (default: 7).",
        },
        "rate": {
            "type": "number",
            "minimum": 0,
            "default": DEFAULT_USD_PER_MTOK,
            "description": "Blended USD rate per 1M tokens (default: 3.0).",
        },
    }
    schema = {
        "type": "object",
        "properties": common_props,
        "additionalProperties": False,
    }
    return [
        {
            "name": "open_dashboard",
            "title": "Centsible Live Dashboard",
            "description": "Open the realtime Centsible status, token savings, loop guard, and live event dashboard.",
            "inputSchema": schema,
            "_meta": {
                "ui": {"resourceUri": APP_URI},
                "com.google.antigravity/ui": {
                    "entrypoints": [
                        {"type": "aux_pane", "title": "Centsible Status"},
                        {"type": "global", "title": "Centsible"},
                    ]
                },
            },
        },
        {
            "name": "get_dashboard_data",
            "title": "Get Centsible Live Metrics",
            "description": "Fetch live Centsible stats, config status, and recent events for the dashboard UI.",
            "inputSchema": schema,
            "_meta": {
                "ui": {"visibility": ["app"]},
            },
        },
    ]


def _format_summary_text(payload: Dict[str, Any]) -> str:
    m = payload.get("metrics", {})
    s = payload.get("status", {})
    scope_label = (
        f"all projects ({m.get('projects', 0)})"
        if payload.get("scope") == "all"
        else payload.get("project_name", "project")
    )
    net = m.get("attributed_net_saved_tokens", m.get("net_saved_tokens", 0))
    return (
        f"Centsible Live Dashboard [{scope_label}]: "
        f"{net:+,} verified net tokens (${m.get('saved_usd', 0.0):.4f}), "
        f"{m.get('total_facts', 0)} facts ({m.get('fact_hits', 0)} hits), "
        f"{m.get('first_try_success_rate', 100.0):.1f}% 1st-try, "
        f"{m.get('loop_interventions', 0)} loop stops (mode {s.get('mode', 'normal')})."
    )


def handle_rpc_request(req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Handle one JSON-RPC 2.0 request. Returns None for notifications."""
    req_id = req.get("id")
    method = req.get("method", "")
    params = req.get("params") or {}

    if req_id is None:
        return None

    def ok(result: Dict[str, Any]) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "result": result}

    def err(code: int, message: str) -> Dict[str, Any]:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}

    if method == "initialize":
        proto = params.get("protocolVersion") or DEFAULT_PROTOCOL_VERSION
        return ok({
            "protocolVersion": proto,
            "capabilities": {
                "tools": {},
                "resources": {},
            },
            "serverInfo": {
                "name": "centsible",
                "version": __version__,
            },
        })

    if method == "ping":
        return ok({})

    if method == "tools/list":
        return ok({"tools": _tool_definitions()})

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name not in ("open_dashboard", "get_dashboard_data"):
            return err(-32602, f"Unknown tool: {name}")
        try:
            payload = collect_dashboard_payload(arguments)
            return ok({
                "content": [{"type": "text", "text": _format_summary_text(payload)}],
                "structuredContent": payload,
                "isError": False,
            })
        except Exception as exc:
            debug_warn("mcp_tools_call", exc)
            return ok({
                "content": [{"type": "text", "text": f"Failed to load centsible metrics: {exc}"}],
                "isError": True,
            })

    if method == "resources/list":
        return ok({
            "resources": [
                {
                    "uri": APP_URI,
                    "name": "Centsible Live Dashboard",
                    "description": "Realtime dashboard for Centsible status, stats, and events",
                    "mimeType": APP_MIME,
                }
            ]
        })

    if method == "resources/read":
        uri = params.get("uri")
        if uri != APP_URI:
            return err(-32002, f"Resource not found: {uri}")
        try:
            html = _load_dashboard_html()
        except OSError as exc:
            return err(-32603, f"Failed to read dashboard HTML: {exc}")
        return ok({
            "contents": [
                {
                    "uri": APP_URI,
                    "mimeType": APP_MIME,
                    "text": html,
                    "_meta": {"ui": {"prefersBorder": False}},
                }
            ]
        })

    return err(-32601, f"Method not found: {method}")


def run_mcp_stdio() -> int:
    """Run the JSON-RPC 2.0 MCP server loop over stdin/stdout."""
    if os.environ.get("CENTSIBLE_DEBUG"):
        sys.stderr.write(f"[centsible:mcp] starting stdio server v{__version__}\n")
        sys.stderr.flush()

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as exc:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {exc}"}}
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
            continue

        if not isinstance(msg, dict):
            continue

        resp = handle_rpc_request(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()

    return 0
