"""Hook handlers for Antigravity and Claude Code, plus the shared engine/payload helpers."""

import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional

# Ensure repo root is in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.facts_engine import (TOOL_CATEGORIES, FactsEngine, debug_warn, detect_user_correction, get_default_facts_path, get_global_facts_path)
from core.loop_guard import (LoopGuard, extract_error_from_payload, resolve_inject_mode, resolve_max_prompt_facts, resolve_reflect)


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


def handle_hook_pre_invocation() -> None:
    """
    Antigravity Hook: PreInvocation.
    Reads input context from stdin, matches facts, and outputs ephemeral injection.
    """
    payload = _read_payload()

    user_input = payload.get("lastUserInput", "")
    workspace_paths = payload.get("workspacePaths", [])
    conversation_id = payload.get("conversationId", "default")

    facts_path = get_default_facts_path(workspace_paths)
    engine = _engine(facts_path)
    guard = LoopGuard(conversation_id)
    seen_ids = guard.get_injected_fact_ids() if conversation_id != "default" else []

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

    ephemeral_msg = engine.format_ephemeral_message(
        matched,
        correction_hint=correction_hint,
        reflect_mode=resolve_reflect(),
    )

    output: Dict[str, Any] = {}
    if ephemeral_msg:
        output = {
            "injectSteps": [
                {
                    "ephemeralMessage": ephemeral_msg
                }
            ]
        }

    _emit(output)


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
    guard.record_post_tool_use(tool_call, error, engine=engine)

    _emit({})


def handle_hook_claude_prompt() -> None:
    """
    Claude Code Hook: UserPromptSubmit.
    Reads prompt and cwd from stdin, outputs additionalContext.
    """
    payload = _read_payload()

    user_prompt = payload.get("prompt", "")
    cwd = payload.get("cwd") or str(Path.cwd())
    session_id = payload.get("session_id", "default")

    facts_path = get_default_facts_path([cwd])
    engine = _engine(facts_path)
    guard = LoopGuard(session_id)
    seen_ids = guard.get_injected_fact_ids() if session_id != "default" else []

    inject_mode = resolve_inject_mode()
    if inject_mode == "off":
        matched = []
    elif inject_mode == "jit":
        all_matched = engine.match(user_prompt, record_hits=False, exclude_ids=seen_ids)
        always_facts = [f for f in all_matched if f.get("category") in ("always", "global") or f.get("pattern") == "*"]
        if always_facts:
            matched = engine.match(user_prompt, record_hits=True, exclude_ids=seen_ids)
            matched = [f for f in matched if f.get("category") in ("always", "global") or f.get("pattern") == "*"]
        else:
            matched = []
    elif inject_mode == "smart":
        # Smart Hybrid: inject top domain & gotcha facts into prompt (capped at max_prompt_facts)
        # Defer pure CLI tool syntax facts to PreToolUse Just-in-Time!
        matched = engine.match(
            user_prompt,
            record_hits=True,
            max_facts=resolve_max_prompt_facts(),
            exclude_categories=TOOL_CATEGORIES,
            exclude_ids=seen_ids,
        )
    else:
        matched = engine.match(user_prompt, record_hits=True, exclude_ids=seen_ids)

    if matched:
        guard.record_injected_facts([f.get("id", "") for f in matched])

    learned_gotcha = engine.auto_learn_from_correction(user_prompt)
    if learned_gotcha:
        guard.record_injected_facts([learned_gotcha.get("id", "")])
        correction_hint: Optional[str] = (
            f"[Centsible Auto-Learned Gotcha] Saved [{learned_gotcha['id']}] "
            f"(trigger: {learned_gotcha['pattern']})"
        )
    else:
        correction_hint = detect_user_correction(user_prompt)

    context_msg = engine.format_ephemeral_message(
        matched,
        correction_hint=correction_hint,
        reflect_mode=resolve_reflect(),
    )

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
    decision, reason = guard.check_pre_tool_use(tool_call, engine=engine)

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
    guard.record_post_tool_use(tool_call, error, engine=engine)

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
