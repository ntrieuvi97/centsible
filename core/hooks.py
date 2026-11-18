"""Hook handlers for Antigravity, Claude Code and Hermes, plus the shared engine/payload helpers."""

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure repo root is in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.facts_engine import (TOOL_CATEGORIES, FactsEngine, debug_warn, detect_user_correction, estimate_tokens, get_default_facts_path, get_global_facts_path)
from core.loop_guard import (LoopGuard, budget_enabled, extract_error_from_payload, resolve_inject_mode, resolve_max_prompt_facts, resolve_reflect)


def _engine(facts_path: Optional[Path] = None) -> FactsEngine:
    """Engine over the project facts plus the global (all-repos) facts layer."""
    return FactsEngine(facts_path, global_path=get_global_facts_path())


def _read_payload() -> Dict[str, Any]:
    """Hook JSON from stdin; empty or malformed input degrades to {} (a hook must never crash the agent)."""
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception as e:
        debug_warn("hook payload", e)
        return {}


def _emit(obj: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _io_tokens(*parts: Any) -> int:
    """Estimated tokens of a tool call's input and output; only computed when a session budget is on."""
    if not budget_enabled():
        return 0
    total = 0
    for p in parts:
        if p:
            total += estimate_tokens(p if isinstance(p, str) else json.dumps(p, ensure_ascii=False, default=str))
    return total


def _prompt_context(user_input: str, workspace_paths: List[str], session_id: str) -> Optional[str]:
    """Facts to inject for this prompt (per the inject mode), plus any learned-correction note. Shared by every host."""
    engine = _engine(get_default_facts_path(workspace_paths))
    guard = LoopGuard(session_id)
    seen_ids = guard.get_injected_fact_ids() if session_id != "default" else []

    inject_mode = resolve_inject_mode()
    if inject_mode == "off":
        matched = []
    elif inject_mode == "jit":
        all_matched = engine.match(user_input, record_hits=False, exclude_ids=seen_ids)
        always_facts = [f for f in all_matched if f.get("category") in ("always", "global") or f.get("pattern") == "*"]
        if always_facts:
            matched = engine.match(user_input, record_hits=True, exclude_ids=seen_ids)
            matched = [f for f in matched if f.get("category") in ("always", "global") or f.get("pattern") == "*"]
        else:
            matched = []
    elif inject_mode == "smart":
        # Smart Hybrid: inject top domain & gotcha facts into prompt (capped at max_prompt_facts)
        # Defer pure CLI tool syntax facts to PreToolUse Just-in-Time!
        matched = engine.match(
            user_input,
            record_hits=True,
            max_facts=resolve_max_prompt_facts(),
            exclude_categories=TOOL_CATEGORIES,
            exclude_ids=seen_ids,
        )
    else:
        matched = engine.match(user_input, record_hits=True, exclude_ids=seen_ids)

    if matched:
        guard.record_injected_facts([f.get("id", "") for f in matched])

    learned_gotcha = engine.auto_learn_from_correction(user_input)
    if learned_gotcha:
        guard.record_injected_facts([learned_gotcha.get("id", "")])
        correction_hint: Optional[str] = (
            f"[Centsible Auto-Learned Gotcha] Saved [{learned_gotcha['id']}] "
            f"(trigger: {learned_gotcha['pattern']})"
        )
    else:
        correction_hint = detect_user_correction(user_input)

    return engine.format_ephemeral_message(
        matched,
        correction_hint=correction_hint,
        reflect_mode=resolve_reflect(),
    )


def handle_hook_pre_invocation() -> None:
    """
    Antigravity Hook: PreInvocation.
    Reads input context from stdin, matches facts, and outputs ephemeral injection.
    """
    payload = _read_payload()
    ephemeral_msg = _prompt_context(
        payload.get("lastUserInput", ""), payload.get("workspacePaths", []), payload.get("conversationId", "default")
    )
    _emit({"injectSteps": [{"ephemeralMessage": ephemeral_msg}]} if ephemeral_msg else {})


def handle_hook_pre_tool() -> None:
    """
    Antigravity Hook: PreToolUse.
    Checks loop guard and uninjected tool micro-facts.
    """
    payload = _read_payload()

    tool_call = payload.get("toolCall", {})
    conversation_id = payload.get("conversationId", "default")
    workspace_paths = payload.get("workspacePaths", [])

    engine = _engine(get_default_facts_path(workspace_paths))
    guard = LoopGuard(conversation_id)
    # Hosts that can attach the hint to the tool result (OpenClaw bridge) send blockHints=false, so a hint never
    # stops a command; hosts that cannot (Antigravity) keep the default and get 'ask'.
    block_hints = payload.get("blockHints", True) is not False
    decision, reason = guard.check_pre_tool_use(tool_call, engine=engine, block_hints=block_hints)

    response: Dict[str, Any] = {"decision": decision}
    if reason:
        response["reason"] = reason

    _emit(response)


def handle_hook_post_tool() -> None:
    """
    Antigravity Hook: PostToolUse.
    Records tool outcome to detect failure streaks and auto-learn recoveries.
    """
    payload = _read_payload()

    tool_call = payload.get("toolCall", {})
    conversation_id = payload.get("conversationId", "default")
    workspace_paths = payload.get("workspacePaths", [])
    error = extract_error_from_payload(payload)

    engine = _engine(get_default_facts_path(workspace_paths))
    guard = LoopGuard(conversation_id)
    io = _io_tokens(tool_call.get("args"), payload.get("toolResult") or payload.get("output"))
    guard.record_post_tool_use(tool_call, error, engine=engine, io_tokens=io)

    _emit({})


def handle_hook_claude_prompt() -> None:
    """
    Claude Code Hook: UserPromptSubmit.
    Reads prompt and cwd from stdin, outputs additionalContext.
    """
    payload = _read_payload()
    cwd = payload.get("cwd") or str(Path.cwd())
    context_msg = _prompt_context(payload.get("prompt", ""), [cwd], payload.get("session_id", "default"))

    output: Dict[str, Any] = {}
    if context_msg:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context_msg,
            }
        }

    _emit(output)


def handle_hook_claude_pre_tool() -> None:
    """
    Claude Code Hook: PreToolUse.
    Checks LoopGuard and uninjected tool facts; exits 2 if blocked, exits 0 with additionalContext if allowed.
    """
    payload = _read_payload()

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})
    session_id = payload.get("session_id", "default")
    cwd = payload.get("cwd") or str(Path.cwd())

    tool_call = {
        "name": tool_name,
        "args": tool_input,
    }

    engine = _engine(get_default_facts_path([cwd]))
    guard = LoopGuard(session_id)
    decision, reason = guard.check_pre_tool_use(tool_call, engine=engine, transcript_path=payload.get("transcript_path"))

    if decision != "allow" and reason:
        # In Claude Code, exit code 2 aborts the tool call and prints stderr
        sys.stderr.write(f"\n{reason}\n")
        sys.stderr.flush()
        sys.exit(2)

    if decision == "allow" and reason:
        # Smoothly inject file/module rules & gotchas without aborting Read/Edit
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
                "additionalContext": reason,
            }
        }
        _emit(output)

    sys.exit(0)


def handle_hook_claude_post_tool(failed: bool = False) -> None:
    """
    Claude Code Hook: PostToolUse (success) / PostToolUseFailure (failed=True).
    Records tool outcome to track failure streaks and auto-learn recoveries.
    Claude Code only fires PostToolUse on success, so failures arrive via PostToolUseFailure.
    """
    payload = _read_payload()

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})
    session_id = payload.get("session_id", "default")
    cwd = payload.get("cwd") or str(Path.cwd())
    error = extract_error_from_payload(payload)
    if failed and not error:
        error = "tool failed"

    tool_call = {
        "name": tool_name,
        "args": tool_input,
    }

    engine = _engine(get_default_facts_path([cwd]))
    guard = LoopGuard(session_id)
    io = _io_tokens(tool_input, payload.get("tool_response") or payload.get("error"))
    guard.record_post_tool_use(tool_call, error, engine=engine, io_tokens=io)

    _emit({})


def handle_hook_stop() -> None:
    """
    Antigravity Hook: Stop.
    When the turn completes normally, check if the session uncovered gotchas or new patterns,
    and nudge the agent once to persist them via facts add before closing.
    """
    payload = _read_payload()

    termination_reason = payload.get("terminationReason", "NO_TOOL_CALL")
    # Only nudge on clean/normal completions (NO_TOOL_CALL). Never block user cancellation, limits, or errors.
    if termination_reason != "NO_TOOL_CALL":
        _emit({})
        return

    conversation_id = payload.get("conversationId", "default")
    guard = LoopGuard(conversation_id)
    nudge_msg = guard.consume_wrapup_nudge()

    output: Dict[str, Any] = {}
    if nudge_msg:
        output = {
            "decision": "continue",
            "reason": nudge_msg,
        }

    _emit(output)


def handle_hook_claude_stop() -> None:
    """
    Claude Code Hook: Stop. If the agent hit repeated tool failures or completed significant activity this session,
    block the stop once so it can persist any gotchas or golden facts discovered.
    """
    payload = _read_payload()

    output: Dict[str, Any] = {}
    # stop_hook_active: we already forced a continuation this stop cycle; never loop.
    if not payload.get("stop_hook_active"):
        guard = LoopGuard(payload.get("session_id", "default"))
        nudge_msg = guard.consume_wrapup_nudge()
        if nudge_msg:
            output = {
                "decision": "block",
                "reason": nudge_msg,
            }

    _emit(output)


# --- Hermes shell hooks -------------------------------------------------------------------------------------------
# Hermes pipes {hook_event_name, tool_name, tool_input, session_id, cwd, extra} on stdin and reads JSON on stdout.
# Event-specific fields (user_message, result, attempt, ...) live under `extra`.


def _hermes_extra(payload: Dict[str, Any]) -> Dict[str, Any]:
    extra = payload.get("extra")
    return extra if isinstance(extra, dict) else {}


def _hermes_session(payload: Dict[str, Any]) -> str:
    """Hermes sends session_id as "" for some callers; fall back to the conversation/task id, never a shared bucket."""
    extra = _hermes_extra(payload)
    return str(payload.get("session_id") or extra.get("conversation_id") or extra.get("task_id") or "default")


def _hermes_text(message: Any) -> str:
    """A Hermes user message is a string, or a list of content parts on multimodal turns."""
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        return " ".join(p.get("text", "") for p in message if isinstance(p, dict) and isinstance(p.get("text"), str))
    return ""


def handle_hook_hermes_prompt() -> None:
    """Hermes Hook: pre_llm_call (once per user turn). Returns {"context": ...}, appended to the user message."""
    payload = _read_payload()
    cwd = payload.get("cwd") or str(Path.cwd())
    session_id = _hermes_session(payload)
    context_msg = _prompt_context(_hermes_text(_hermes_extra(payload).get("user_message")), [cwd], session_id)
    _emit({"context": context_msg} if context_msg else {})


def handle_hook_hermes_pre_tool() -> None:
    """Hermes Hook: pre_tool_call. Hermes cannot attach context to a call it lets through, so any guard message
    (loop stop, budget, one-time fact hint) blocks this call; the agent sees it as the tool error and retries."""
    payload = _read_payload()
    cwd = payload.get("cwd") or str(Path.cwd())
    tool_call = {"name": payload.get("tool_name") or "", "args": payload.get("tool_input") or {}}

    engine = _engine(get_default_facts_path([cwd]))
    guard = LoopGuard(_hermes_session(payload))
    _, reason = guard.check_pre_tool_use(tool_call, engine=engine, block_hints=True)
    _emit({"decision": "block", "reason": reason} if reason else {})


def handle_hook_hermes_post_tool() -> None:
    """Hermes Hook: post_tool_call. `extra.result` is the tool's JSON string (terminal adds exit_code / error)."""
    payload = _read_payload()
    cwd = payload.get("cwd") or str(Path.cwd())
    tool_input = payload.get("tool_input") or {}
    tool_call = {"name": payload.get("tool_name") or "", "args": tool_input}
    raw = _hermes_extra(payload).get("result")
    try:
        result = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        result = raw
    if isinstance(result, dict) and isinstance(result.get("exit_code"), int) and result["exit_code"] == 0 and not result.get("error"):
        error = None  # a clean exit: don't let a stray "Exit code" line in the output count as a failure
    else:
        error = extract_error_from_payload({"tool_response": result})

    engine = _engine(get_default_facts_path([cwd]))
    guard = LoopGuard(_hermes_session(payload))
    guard.record_post_tool_use(tool_call, error, engine=engine, io_tokens=_io_tokens(tool_input, raw))
    _emit({})


def handle_hook_hermes_verify() -> None:
    """Hermes Hook: pre_verify (fires when the agent edited code and is about to finish). Nudges at most once."""
    payload = _read_payload()
    if _hermes_extra(payload).get("attempt"):
        _emit({})
        return
    nudge_msg = LoopGuard(_hermes_session(payload)).consume_wrapup_nudge()
    _emit({"action": "continue", "message": nudge_msg} if nudge_msg else {})
