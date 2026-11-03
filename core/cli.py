#!/usr/bin/env python3
"""
CLI entrypoint and Hook Router for agy-token-economizer.
"""

import argparse
import json
import sys
from pathlib import Path

# Ensure repo root is in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.facts_engine import FactsEngine, get_default_facts_path
from core.loop_guard import LoopGuard


def handle_hook_pre_invocation() -> None:
    """
    Antigravity Hook: PreInvocation.
    Reads input context from stdin, matches facts, and outputs ephemeral injection.
    """
    try:
        raw_input = sys.stdin.read()
        payload: Dict[str, Any] = json.loads(raw_input) if raw_input.strip() else {}
    except Exception:
        payload = {}

    user_input = payload.get("lastUserInput", "")
    workspace_paths = payload.get("workspacePaths", [])

    facts_path = get_default_facts_path(workspace_paths)
    engine = FactsEngine(facts_path)

    matched = engine.match(user_input, record_hits=True)
    ephemeral_msg = engine.format_ephemeral_message(matched)

    output: Dict[str, Any] = {}
    if ephemeral_msg:
        output = {
            "injectSteps": [
                {
                    "ephemeralMessage": ephemeral_msg
                }
            ]
        }

    sys.stdout.write(json.dumps(output, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle_hook_pre_tool() -> None:
    """
    Antigravity Hook: PreToolUse.
    Checks loop guard to prevent runaway tool loops or repeated errors.
    """
    try:
        raw_input = sys.stdin.read()
        payload: Dict[str, Any] = json.loads(raw_input) if raw_input.strip() else {}
    except Exception:
        payload = {}

    tool_call = payload.get("toolCall", {})
    conversation_id = payload.get("conversationId", "default")

    guard = LoopGuard(conversation_id)
    decision, reason = guard.check_pre_tool_use(tool_call)

    response: Dict[str, Any] = {"decision": decision}
    if reason:
        response["reason"] = reason

    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle_hook_post_tool() -> None:
    """
    Antigravity Hook: PostToolUse.
    Records tool outcome to detect failure streaks.
    """
    try:
        raw_input = sys.stdin.read()
        payload: Dict[str, Any] = json.loads(raw_input) if raw_input.strip() else {}
    except Exception:
        payload = {}

    tool_call = payload.get("toolCall", {})
    conversation_id = payload.get("conversationId", "default")
    error = payload.get("error")

    guard = LoopGuard(conversation_id)
    guard.record_post_tool_use(tool_call, error)

    sys.stdout.write(json.dumps({}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle_hook_claude_prompt() -> None:
    """
    Claude Code Hook: UserPromptSubmit.
    Reads prompt and cwd from stdin, outputs additionalContext.
    """
    try:
        raw_input = sys.stdin.read()
        payload: Dict[str, Any] = json.loads(raw_input) if raw_input.strip() else {}
    except Exception:
        payload = {}

    user_prompt = payload.get("prompt", "")
    cwd = payload.get("cwd") or str(Path.cwd())

    facts_path = get_default_facts_path([cwd])
    engine = FactsEngine(facts_path)

    matched = engine.match(user_prompt, record_hits=True)
    context_msg = engine.format_ephemeral_message(matched)

    output: Dict[str, Any] = {}
    if context_msg:
        output = {
            "hookSpecificOutput": {
                "additionalContext": context_msg
            }
        }

    sys.stdout.write(json.dumps(output, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle_hook_claude_pre_tool() -> None:
    """
    Claude Code Hook: PreToolUse.
    Checks LoopGuard; exits 2 if runaway loop detected, exits 0 if allowed.
    """
    try:
        raw_input = sys.stdin.read()
        payload: Dict[str, Any] = json.loads(raw_input) if raw_input.strip() else {}
    except Exception:
        payload = {}

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})
    session_id = payload.get("session_id", "default")

    tool_call = {
        "name": tool_name,
        "args": tool_input,
    }

    guard = LoopGuard(session_id)
    decision, reason = guard.check_pre_tool_use(tool_call)

    if decision != "allow" and reason:
        # In Claude Code, exit code 2 aborts the tool call and prints stderr
        sys.stderr.write(f"\n{reason}\n")
        sys.stderr.flush()
        sys.exit(2)

    sys.exit(0)


def handle_hook_claude_post_tool() -> None:
    """
    Claude Code Hook: PostToolUse.
    Records tool outcome to track failure streaks.
    """
    try:
        raw_input = sys.stdin.read()
        payload: Dict[str, Any] = json.loads(raw_input) if raw_input.strip() else {}
    except Exception:
        payload = {}

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})
    session_id = payload.get("session_id", "default")
    error = payload.get("error")

    tool_call = {
        "name": tool_name,
        "args": tool_input,
    }

    guard = LoopGuard(session_id)
    guard.record_post_tool_use(tool_call, error)

    sys.stdout.write(json.dumps({}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="centsible",
        description="centsible: Experience-driven cost and token optimizer for AI coding agents",
    )
    subparsers = parser.add_subparsers(dest="command")

    # Hook group
    hook_parser = subparsers.add_parser("hook", help="Internal hook router for Antigravity")
    hook_sub = hook_parser.add_subparsers(dest="hook_type")
    hook_sub.add_parser("pre-invocation", help="Antigravity PreInvocation hook")
    hook_sub.add_parser("pre-tool", help="Antigravity PreToolUse hook")
    hook_sub.add_parser("post-tool", help="Antigravity PostToolUse hook")

    # Claude Code hooks
    hook_sub.add_parser("claude-prompt", help="Claude Code UserPromptSubmit hook")
    hook_sub.add_parser("claude-pre-tool", help="Claude Code PreToolUse hook")
    hook_sub.add_parser("claude-post-tool", help="Claude Code PostToolUse hook")

    # Facts group
    facts_parser = subparsers.add_parser("facts", help="Manage repository micro-facts")
    facts_sub = facts_parser.add_subparsers(dest="facts_action")

    # facts list
    facts_list = facts_sub.add_parser("list", help="List all stored micro-facts")
    facts_list.add_argument("--category", help="Filter by category")

    # facts add
    facts_add = facts_sub.add_parser("add", help="Add or update a micro-fact")
    facts_add.add_argument("--pattern", "-p", required=True, help="Regex pattern or keyword to trigger")
    facts_add.add_argument("--fact", "-f", required=True, help="One-line fact to inject")
    facts_add.add_argument("--category", "-c", default="general", help="Category tag")
    facts_add.add_argument("--id", help="Optional explicit ID")

    # facts remove
    facts_del = facts_sub.add_parser("remove", help="Remove a fact by ID or pattern")
    facts_del.add_argument("target", help="Fact ID or regex pattern to remove")

    # facts test
    facts_test = facts_sub.add_parser("test", help="Test fact matching against a prompt string")
    facts_test.add_argument("prompt", help="User input text to test")

    # status command
    subparsers.add_parser("status", help="Show system status and facts count")

    args = parser.parse_args()

    # Route hook commands
    if args.command == "hook":
        if args.hook_type == "pre-invocation":
            handle_hook_pre_invocation()
            return 0
        elif args.hook_type == "pre-tool":
            handle_hook_pre_tool()
            return 0
        elif args.hook_type == "post-tool":
            handle_hook_post_tool()
            return 0
        elif args.hook_type == "claude-prompt":
            handle_hook_claude_prompt()
            return 0
        elif args.hook_type == "claude-pre-tool":
            handle_hook_claude_pre_tool()
            return 0
        elif args.hook_type == "claude-post-tool":
            handle_hook_claude_post_tool()
            return 0
        else:
            hook_parser.print_help()
            return 1

    # Route facts commands
    engine = FactsEngine()

    if args.command == "facts":
        if args.facts_action == "list":
            facts = engine.list_facts(category=args.category)
            if not facts:
                print("No micro-facts stored yet. Add one with 'centsible facts add --pattern ... --fact ...'")
                return 0
            print(f"Stored Micro-Facts ({len(facts)}):")
            print(f"Path: {engine.storage_path}\n")
            for f in facts:
                print(f"• [{f.get('id')}] (hits: {f.get('hits', 0)})")
                print(f"  Pattern: {f.get('pattern')}")
                print(f"  Fact:    {f.get('fact')}\n")
            return 0

        elif args.facts_action == "add":
            entry = engine.add_fact(
                pattern=args.pattern,
                fact=args.fact,
                category=args.category,
                fact_id=args.id,
            )
            print(f"✓ Added micro-fact [{entry['id']}]: '{entry['fact']}' (Trigger: {entry['pattern']})")
            return 0

        elif args.facts_action == "remove":
            removed = engine.remove_fact(args.target)
            if removed:
                print(f"✓ Removed fact matching: {args.target}")
            else:
                print(f"✗ No fact found matching ID or pattern: {args.target}")
            return 0 if removed else 1

        elif args.facts_action == "test":
            matched = engine.match(args.prompt, record_hits=False)
            print(f"Prompt: \"{args.prompt}\"")
            print(f"Matched {len(matched)} fact(s):")
            for m in matched:
                print(f"- [{m.get('id')}] {m.get('fact')} (Pattern: {m.get('pattern')})")
            return 0

        else:
            facts_parser.print_help()
            return 1

    elif args.command == "status":
        facts = engine.list_facts()
        total_hits = sum(f.get("hits", 0) for f in facts)
        print("=== Centsible Status 🪙 ===")
        print(f"• Facts Storage:  {engine.storage_path}")
        print(f"• Total Facts:    {len(facts)}")
        print(f"• Total Hits:     {total_hits}")
        print("• Loop Guard:     Enabled (pre-tool threshold: 3 identical, 2 errors)")
        print("• Status:         Active")
        return 0

    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
