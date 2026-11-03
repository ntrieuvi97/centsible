#!/usr/bin/env python3
"""
Loop Guard for centsible.
Detects and breaks repetitive failure loops and runaway identical tool calls.
"""

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.facts_engine import DEFAULT_SAVED_TOKENS_PER_LOOP_STOP, FactsEngine, find_common_bin

RECOVERY_WINDOW = 10  # tool calls a failed command stays eligible for auto-learn
MUTATING_TOOLS = {
    "Edit", "Write", "MultiEdit", "NotebookEdit",
    "edit_file", "write_file", "replace_file_content", "multi_replace_file_content", "write_to_file",
}
VOLATILE_ARG_KEYS = {"toolSummary", "WaitMsBeforeAsync", "description", "timeout", "IsDaemon"}
TRIVIAL_CLI_FLAGS_RE = re.compile(
    r"(?:^|\s)(?:-v+|-q|--quiet|--verbose|--tb=(?:short|long|line|native|no)|--no-header)(?=\s|$)"
)


# config key -> (env var, default). Resolution order: env var, then config.json, then default.
CONFIG_KEYS = {
    "max_consecutive_errors": ("CENTSIBLE_MAX_CONSECUTIVE_ERRORS", 2),
    "max_identical_calls": ("CENTSIBLE_MAX_IDENTICAL_CALLS", 3),
}


def get_config_path() -> Path:
    env_home = os.environ.get("CENTSIBLE_HOME")
    return (Path(env_home).expanduser() if env_home else Path.home() / ".centsible") / "config.json"


def load_config() -> Dict[str, Any]:
    try:
        data = json.loads(get_config_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_config(cfg: Dict[str, Any]) -> None:
    path = get_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def resolve_threshold(key: str) -> int:
    """Positive int from env var, else config.json, else default; invalid values are skipped."""
    env_name, default = CONFIG_KEYS[key]
    for raw in (os.environ.get(env_name), load_config().get(key)):
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value >= 1:
            return value
    return default


def normalize_command(cmd: str) -> str:
    """Normalize CLI command string by stripping cosmetic flags and extra whitespace."""
    if not cmd:
        return ""
    cleaned = TRIVIAL_CLI_FLAGS_RE.sub(" ", cmd)
    return " ".join(cleaned.strip().split())


def extract_error_from_payload(payload: Dict[str, Any]) -> Optional[str]:
    """
    Extract error message from Antigravity or Claude Code PostToolUse payload.
    Detects explicit error fields, non-zero exit codes, and output exit markers.
    """
    if not payload:
        return None

    err = payload.get("error")
    if err:
        return str(err)

    if payload.get("is_error") is True:
        return str(payload.get("tool_response") or payload.get("output") or "tool error")

    for code_key in ("exitCode", "exit_code", "returncode"):
        code = payload.get(code_key)
        if isinstance(code, int) and code != 0:
            return f"exit code {code}"

    resp = payload.get("tool_response") or payload.get("toolResult") or payload.get("output")
    if isinstance(resp, dict):
        if resp.get("error"):
            return str(resp["error"])
        if resp.get("is_error") is True:
            return str(resp.get("output") or resp.get("stderr") or "tool error")
        for code_key in ("exitCode", "exit_code", "returncode"):
            code = resp.get(code_key)
            if isinstance(code, int) and code != 0:
                return f"exit code {code}"
        resp_text = str(resp.get("output") or resp.get("stdout") or "")
    elif isinstance(resp, str):
        resp_text = resp
    else:
        resp_text = ""

    if resp_text:
        m = re.search(r"(?:Command exited with code|Exit code:?)\s+([1-9]\d*)", resp_text, re.IGNORECASE)
        if m:
            return f"exit code {m.group(1)}"

    return None


def get_state_path(conversation_id: Optional[str]) -> Path:
    safe_id = "".join(c for c in (conversation_id or "default") if c.isalnum() or c in "-_")
    # Secure user-isolated temp directory (prevents symlink/collision attacks on multi-user systems)
    uid = os.getuid() if hasattr(os, "getuid") else "user"
    te_dir = Path(tempfile.gettempdir()) / f"te_{uid}"
    try:
        te_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    except Exception:
        te_dir = Path(tempfile.gettempdir())
    return te_dir / f"loop_guard_{safe_id}.json"


class LoopGuard:
    def __init__(
        self,
        conversation_id: Optional[str] = None,
        max_identical_calls: Optional[int] = None,
        max_consecutive_errors: Optional[int] = None,
    ):
        self.conversation_id = conversation_id
        self.state_path = get_state_path(conversation_id)
        # Explicit args win, then env vars, then ~/.centsible/config.json, then the built-in defaults.
        self.max_identical_calls = max_identical_calls or resolve_threshold("max_identical_calls")
        self.max_consecutive_errors = max_consecutive_errors or resolve_threshold("max_consecutive_errors")

    def _default_state(self) -> Dict[str, Any]:
        return {
            "history": [],
            "consecutive_identical_count": 0,
            "consecutive_errors_count": 0,
            "last_call_hash": None,
            "last_failed_cmd": None,
            "calls_since_failed_cmd": 0,
            "injected_fact_ids": [],
            "interventions": 0,
        }

    def _load_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return self._default_state()
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                defaults = self._default_state()
                for k, v in defaults.items():
                    data.setdefault(k, v)
                return data
        except Exception:
            return self._default_state()

    def _save_state(self, state: Dict[str, Any]) -> None:
        try:
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False)
        except Exception:
            pass

    def record_injected_facts(self, fact_ids: List[str]) -> None:
        """Record fact IDs injected at turn start so PreToolUse knows what agent already saw."""
        state = self._load_state()
        existing = set(state.get("injected_fact_ids", []))
        existing.update(fid for fid in fact_ids if fid)
        state["injected_fact_ids"] = sorted(existing)
        self._save_state(state)

    @staticmethod
    def _compute_call_hash(tool_name: str, args: Dict[str, Any]) -> str:
        clean_args: Dict[str, Any] = {}
        for k, v in (args or {}).items():
            if k in VOLATILE_ARG_KEYS:
                continue
            if k in ("CommandLine", "command") and isinstance(v, str):
                clean_args[k] = normalize_command(v)
            else:
                clean_args[k] = v
        serialized = json.dumps({"name": tool_name, "args": clean_args}, sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]

    def check_pre_tool_use(
        self,
        tool_call: Dict[str, Any],
        engine: Optional[FactsEngine] = None,
    ) -> Tuple[str, Optional[str]]:
        """
        Check before a tool executes.
        Returns (decision, reason).
        decision is 'allow', 'ask', or 'deny'.
        """
        tool_name = tool_call.get("name", "")
        args = tool_call.get("args", {})
        call_hash = self._compute_call_hash(tool_name, args)

        state = self._load_state()

        if state.get("last_call_hash") == call_hash:
            state["consecutive_identical_count"] = state.get("consecutive_identical_count", 1) + 1
        else:
            state["consecutive_identical_count"] = 1
            state["last_call_hash"] = call_hash

        # Trigger 1: Too many identical invocations
        if state["consecutive_identical_count"] >= self.max_identical_calls:
            state["interventions"] = state.get("interventions", 0) + 1
            self._save_state(state)
            if engine:
                engine.update_metrics(
                    loop_interventions=1,
                    saved_loop_tokens=DEFAULT_SAVED_TOKENS_PER_LOOP_STOP,
                )
            cmd = args.get("CommandLine") or args.get("command") or tool_name
            reason = (
                f"[LoopGuard Alert] Tool '{tool_name}' with arguments ({cmd}) has been called "
                f"{state['consecutive_identical_count']} times consecutively without variance. "
                "Halting turn to prevent token waste. Please redirect strategy."
            )
            return "ask", reason

        # Trigger 2: Too many consecutive errors on same target
        if state.get("consecutive_errors_count", 0) >= self.max_consecutive_errors:
            state["interventions"] = state.get("interventions", 0) + 1
            # A blocked call never reaches PostToolUse, so nothing else could reset this: block once, then release.
            errors = state["consecutive_errors_count"]
            state["consecutive_errors_count"] = 0
            self._save_state(state)
            if engine:
                engine.update_metrics(
                    loop_interventions=1,
                    saved_loop_tokens=DEFAULT_SAVED_TOKENS_PER_LOOP_STOP,
                )
            reason = (
                f"[LoopGuard Alert] {errors} consecutive failures detected. "
                "Circuit breaker activated. Review error cause before retrying."
            )
            return "ask", reason

        # Trigger 3: PreToolUse Micro-Fact check (when prompt didn't mention the tool, and required flags are missing)
        if engine:
            seen_ids = state.get("injected_fact_ids", [])
            uninjected = engine.match_tool_call(
                tool_call,
                exclude_ids=seen_ids,
                only_missing_flags=True,
                record_hits=True,
            )
            if uninjected:
                seen_set = set(seen_ids)
                seen_set.update(f.get("id") for f in uninjected if f.get("id"))
                state["injected_fact_ids"] = sorted(seen_set)
                self._save_state(state)
                facts_str = "; ".join(f.get("fact", "") for f in uninjected)
                reason = (
                    f"[Centsible Micro-Skill Hint] Before executing '{tool_name}', "
                    f"apply verified repository fact: {facts_str}"
                )
                cmd_arg = args.get("CommandLine") or args.get("command")
                is_blocking_cli_hint = bool(
                    cmd_arg and any(f.get("category", "general") not in ("rule", "gotcha") for f in uninjected)
                )
                return ("ask" if is_blocking_cli_hint else "allow"), reason

        self._save_state(state)
        return "allow", None

    def record_post_tool_use(
        self,
        tool_call: Dict[str, Any],
        error: Optional[str] = None,
        engine: Optional[FactsEngine] = None,
    ) -> None:
        """Record outcome after tool execution and update repository telemetry."""
        state = self._load_state()
        args = tool_call.get("args") or {}
        cmd = args.get("CommandLine") or args.get("command")
        prev_errors = state.get("consecutive_errors_count", 0)

        if error:
            state["consecutive_errors_count"] = prev_errors + 1
            if cmd:
                state["last_failed_cmd"] = cmd
                state["calls_since_failed_cmd"] = 0
            if engine:
                engine.update_metrics(tool_calls_total=1, tool_calls_failed=1)
        else:
            is_first_try = (prev_errors == 0 and state.get("consecutive_identical_count", 1) <= 1)
            state["consecutive_errors_count"] = 0
            tool_name_cur = tool_call.get("name", "")
            failed_cmd = state.get("last_failed_cmd")
            if tool_name_cur in MUTATING_TOOLS:
                # Code/config was modified after failure -> failure was likely a code bug, not a CLI quirk
                state["last_failed_cmd"] = None
                state["calls_since_failed_cmd"] = 0
            elif failed_cmd and cmd:
                if engine:
                    engine.auto_learn_from_recovery(failed_cmd, cmd)
                # Keep the failure across unrelated commands, but not forever.
                if find_common_bin(cmd) == find_common_bin(failed_cmd) or state.get("calls_since_failed_cmd", 0) >= RECOVERY_WINDOW:
                    state["last_failed_cmd"] = None
            # Read-only tools (Read/Grep/Glob) between failure and retry preserve last_failed_cmd.
            if engine:
                engine.update_metrics(
                    tool_calls_total=1,
                    first_try_successes=1 if is_first_try else 0,
                )

        # Keep last 20 entries in history for debugging/tracing
        tool_name = tool_call.get("name", "")
        history = state.get("history", [])
        history.append({
            "tool": tool_name,
            "error": bool(error),
            "error_msg": str(error)[:100] if error else None,
        })
        state["history"] = history[-20:]
        if state.get("last_failed_cmd") and not error:
            state["calls_since_failed_cmd"] = state.get("calls_since_failed_cmd", 0) + 1

        self._save_state(state)

    def reset(self) -> None:
        """Clear state for this conversation."""
        if self.state_path.exists():
            try:
                self.state_path.unlink()
            except Exception:
                pass
