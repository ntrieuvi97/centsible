#!/usr/bin/env python3
"""
Loop Guard for agy-token-economizer.
Detects and breaks repetitive failure loops and runaway identical tool calls.
"""

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def get_state_path(conversation_id: Optional[str]) -> Path:
    safe_id = "".join(c for c in (conversation_id or "default") if c.isalnum() or c in "-_")
    return Path(tempfile.gettempdir()) / f"te_loop_guard_{safe_id}.json"


class LoopGuard:
    def __init__(
        self,
        conversation_id: Optional[str] = None,
        max_identical_calls: int = 3,
        max_consecutive_errors: int = 2,
    ):
        self.conversation_id = conversation_id
        self.state_path = get_state_path(conversation_id)
        self.max_identical_calls = max_identical_calls
        self.max_consecutive_errors = max_consecutive_errors

    def _load_state(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            return {
                "history": [],
                "consecutive_identical_count": 0,
                "consecutive_errors_count": 0,
                "last_call_hash": None,
            }
        try:
            with open(self.state_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {
                "history": [],
                "consecutive_identical_count": 0,
                "consecutive_errors_count": 0,
                "last_call_hash": None,
            }

    def _save_state(self, state: Dict[str, Any]) -> None:
        try:
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False)
        except Exception:
            pass

    @staticmethod
    def _compute_call_hash(tool_name: str, args: Dict[str, Any]) -> str:
        serialized = json.dumps({"name": tool_name, "args": args}, sort_keys=True)
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]

    def check_pre_tool_use(self, tool_call: Dict[str, Any]) -> Tuple[str, Optional[str]]:
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

        self._save_state(state)

        # Trigger 1: Too many identical invocations
        if state["consecutive_identical_count"] >= self.max_identical_calls:
            cmd = args.get("CommandLine") or args.get("command") or tool_name
            reason = (
                f"[LoopGuard Alert] Tool '{tool_name}' with arguments ({cmd}) has been called "
                f"{state['consecutive_identical_count']} times consecutively without variance. "
                "Halting turn to prevent token waste. Please redirect strategy."
            )
            return "ask", reason

        # Trigger 2: Too many consecutive errors on same target
        if state.get("consecutive_errors_count", 0) >= self.max_consecutive_errors:
            reason = (
                f"[LoopGuard Alert] {state['consecutive_errors_count']} consecutive failures detected. "
                "Circuit breaker activated. Review error cause before retrying."
            )
            return "ask", reason

        return "allow", None

    def record_post_tool_use(self, tool_call: Dict[str, Any], error: Optional[str] = None) -> None:
        """Record outcome after tool execution."""
        state = self._load_state()

        if error:
            state["consecutive_errors_count"] = state.get("consecutive_errors_count", 0) + 1
        else:
            state["consecutive_errors_count"] = 0

        # Keep last 20 entries in history for debugging/tracing
        tool_name = tool_call.get("name", "")
        history = state.get("history", [])
        history.append({
            "tool": tool_name,
            "error": bool(error),
            "error_msg": str(error)[:100] if error else None,
        })
        state["history"] = history[-20:]

        self._save_state(state)

    def reset(self) -> None:
        """Clear state for this conversation."""
        if self.state_path.exists():
            try:
                self.state_path.unlink()
            except Exception:
                pass
