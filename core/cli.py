#!/usr/bin/env python3
"""
CLI entrypoint and Hook Router for centsible.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure repo root is in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import __version__
from core.facts_engine import DEFAULT_USD_PER_MTOK, FactsEngine, get_default_facts_path
from core.loop_guard import LoopGuard, extract_error_from_payload


def analyze_transcript(transcript_path: Path) -> Dict[str, Any]:
    """
    Analyze an Antigravity or Claude Code JSONL session transcript to measure
    tool error rates, retry streaks, discovery steps, and token efficiency.
    """
    stats: Dict[str, Any] = {
        "path": str(transcript_path),
        "total_steps": 0,
        "user_turns": 0,
        "tool_calls": 0,
        "command_calls": 0,
        "failed_calls": 0,
        "retry_streaks": 0,
        "wasted_retry_calls": 0,
        "discovery_steps_to_first_cmd": 0,
        "first_try_cmd_successes": 0,
        "first_try_cmd_rate": 100.0,
    }
    if not transcript_path.exists():
        return stats

    saw_first_cmd = False
    discovery_count = 0
    consecutive_errors = 0

    with open(transcript_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                step = json.loads(line)
            except json.JSONDecodeError:
                continue

            stats["total_steps"] += 1
            step_type = step.get("type") or step.get("role") or ""
            if step_type in ("USER_INPUT", "user"):
                stats["user_turns"] += 1

            tool_calls = step.get("tool_calls") or []
            if not tool_calls and step.get("tool_name"):
                tool_calls = [{"name": step.get("tool_name"), "args": step.get("tool_input", {})}]

            step_failed = (
                step.get("status") == "ERROR"
                or extract_error_from_payload(step) is not None
                or ("Command exited with code " in str(step.get("content", ""))
                    and "Command exited with code 0" not in str(step.get("content", "")))
            )

            for tc in tool_calls:
                stats["tool_calls"] += 1
                t_name = str(tc.get("name") or tc.get("tool") or "")
                is_cmd = t_name in ("run_command", "Bash", "bash", "execute_command")
                if not saw_first_cmd:
                    if is_cmd:
                        saw_first_cmd = True
                        stats["discovery_steps_to_first_cmd"] = discovery_count
                    else:
                        discovery_count += 1
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
                else:
                    consecutive_errors = 0

    if stats["command_calls"] > 0:
        stats["first_try_cmd_rate"] = round(
            stats["first_try_cmd_successes"] / stats["command_calls"] * 100.0, 1
        )
    return stats


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
    conversation_id = payload.get("conversationId", "default")

    facts_path = get_default_facts_path(workspace_paths)
    engine = FactsEngine(facts_path)

    matched = engine.match(user_input, record_hits=True)
    if matched:
        guard = LoopGuard(conversation_id)
        guard.record_injected_facts([f.get("id", "") for f in matched])

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
    Checks loop guard and uninjected tool micro-facts.
    """
    try:
        raw_input = sys.stdin.read()
        payload: Dict[str, Any] = json.loads(raw_input) if raw_input.strip() else {}
    except Exception:
        payload = {}

    tool_call = payload.get("toolCall", {})
    conversation_id = payload.get("conversationId", "default")
    workspace_paths = payload.get("workspacePaths", [])

    engine = FactsEngine(get_default_facts_path(workspace_paths))
    guard = LoopGuard(conversation_id)
    decision, reason = guard.check_pre_tool_use(tool_call, engine=engine)

    response: Dict[str, Any] = {"decision": decision}
    if reason:
        response["reason"] = reason

    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle_hook_post_tool() -> None:
    """
    Antigravity Hook: PostToolUse.
    Records tool outcome to detect failure streaks and auto-learn recoveries.
    """
    try:
        raw_input = sys.stdin.read()
        payload: Dict[str, Any] = json.loads(raw_input) if raw_input.strip() else {}
    except Exception:
        payload = {}

    tool_call = payload.get("toolCall", {})
    conversation_id = payload.get("conversationId", "default")
    workspace_paths = payload.get("workspacePaths", [])
    error = extract_error_from_payload(payload)

    engine = FactsEngine(get_default_facts_path(workspace_paths))
    guard = LoopGuard(conversation_id)
    guard.record_post_tool_use(tool_call, error, engine=engine)

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
    session_id = payload.get("session_id", "default")

    facts_path = get_default_facts_path([cwd])
    engine = FactsEngine(facts_path)

    matched = engine.match(user_prompt, record_hits=True)
    if matched:
        guard = LoopGuard(session_id)
        guard.record_injected_facts([f.get("id", "") for f in matched])

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
    Checks LoopGuard and uninjected tool facts; exits 2 if blocked, exits 0 if allowed.
    """
    try:
        raw_input = sys.stdin.read()
        payload: Dict[str, Any] = json.loads(raw_input) if raw_input.strip() else {}
    except Exception:
        payload = {}

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})
    session_id = payload.get("session_id", "default")
    cwd = payload.get("cwd") or str(Path.cwd())

    tool_call = {
        "name": tool_name,
        "args": tool_input,
    }

    engine = FactsEngine(get_default_facts_path([cwd]))
    guard = LoopGuard(session_id)
    decision, reason = guard.check_pre_tool_use(tool_call, engine=engine)

    if decision != "allow" and reason:
        # In Claude Code, exit code 2 aborts the tool call and prints stderr
        sys.stderr.write(f"\n{reason}\n")
        sys.stderr.flush()
        sys.exit(2)

    sys.exit(0)


def handle_hook_claude_post_tool() -> None:
    """
    Claude Code Hook: PostToolUse.
    Records tool outcome to track failure streaks and auto-learn recoveries.
    """
    try:
        raw_input = sys.stdin.read()
        payload: Dict[str, Any] = json.loads(raw_input) if raw_input.strip() else {}
    except Exception:
        payload = {}

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {})
    session_id = payload.get("session_id", "default")
    cwd = payload.get("cwd") or str(Path.cwd())
    error = extract_error_from_payload(payload)

    tool_call = {
        "name": tool_name,
        "args": tool_input,
    }

    engine = FactsEngine(get_default_facts_path([cwd]))
    guard = LoopGuard(session_id)
    guard.record_post_tool_use(tool_call, error, engine=engine)

    sys.stdout.write(json.dumps({}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="centsible",
        description="centsible: Experience-driven cost and token optimizer for AI coding agents",
    )
    parser.add_argument("--version", "-V", action="version", version=f"centsible {__version__}")
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

    # stats command
    stats_parser = subparsers.add_parser("stats", help="Show token & cost savings metrics and session health")
    stats_parser.add_argument("--rate", type=float, default=DEFAULT_USD_PER_MTOK, help="Blended USD rate per 1M tokens (default: 3.0)")
    stats_parser.add_argument("--transcript", help="Optional path to a session transcript (.jsonl) to analyze")
    stats_parser.add_argument("--json", action="store_true", help="Output metrics as JSON")

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
        summary = engine.get_metrics_summary()
        print("=== Centsible Status 🪙 ===")
        print(f"• Facts Storage:  {engine.storage_path}")
        print(f"• Total Facts:    {summary['total_facts']}")
        print(f"• Total Hits:     {summary['fact_hits']}")
        print(f"• Loop Stops:     {summary['loop_interventions']}")
        print(f"• Tokens Saved:   ~{summary['net_saved_tokens']:,} tokens ({summary['saved_cents']:.2f}¢ / ${summary['saved_usd']:.4f})")
        print(f"• 1st-Try Rate:   {summary['first_try_success_rate']:.1f}% ({summary['tool_calls_total']} tool calls)")
        print("• Loop Guard:     Enabled (pre-tool threshold: 3 identical, 2 errors)")
        print("• Status:         Active")
        return 0

    elif args.command == "stats":
        summary = engine.get_metrics_summary(usd_per_mtok=args.rate)
        transcript_report = analyze_transcript(Path(args.transcript)) if args.transcript else None
        if args.json:
            payload_out: Dict[str, Any] = {"metrics": summary}
            if transcript_report:
                payload_out["transcript"] = transcript_report
            print(json.dumps(payload_out, ensure_ascii=False, indent=2))
            return 0

        print("=== Centsible Efficiency & ROI Report 🪙⚡ ===")
        print(f"• Storage Path:         {engine.storage_path}")
        print(f"• Active Micro-Facts:   {summary['total_facts']} (Auto-learned: {summary['auto_learned_facts']})")
        print(f"• Total Fact Hits:      {summary['fact_hits']} (Prompt: {summary['prompt_injections']}, Pre-Tool Hints: {summary['tool_hint_hits']})")
        print(f"• Loop Interventions:   {summary['loop_interventions']} halted runaway loops")
        print(f"• Tool Execution:       {summary['tool_calls_total']} calls | {summary['first_try_success_rate']:.1f}% 1st-try success | {summary['tool_error_rate']:.1f}% error rate")
        print("--- Token & Cost Accounting ---")
        print(f"• Discovery Saved:      +{summary['saved_discovery_tokens']:,} tokens")
        print(f"• Loop Guard Saved:     +{summary['saved_loop_tokens']:,} tokens")
        print(f"• Ephemeral Overhead:   -{summary['injected_tokens']:,} tokens")
        print(f"• Net Tokens Saved:     {summary['net_saved_tokens']:,} tokens (ROI: {summary['roi_multiplier']}x)")
        print(f"• Estimated Savings:    {summary['saved_cents']:.2f}¢ (${summary['saved_usd']:.4f} @ ${args.rate:.2f}/1M tok)")

        if transcript_report:
            print("--- Session Transcript Analysis ---")
            print(f"• Transcript:           {transcript_report['path']}")
            print(f"• Steps / User Turns:   {transcript_report['total_steps']} steps / {transcript_report['user_turns']} turns")
            print(f"• Tool / Cmd Calls:     {transcript_report['tool_calls']} tools ({transcript_report['command_calls']} commands, {transcript_report['failed_calls']} failed)")
            print(f"• 1st-Try Cmd Success:  {transcript_report['first_try_cmd_rate']:.1f}% (Discovery steps before 1st cmd: {transcript_report['discovery_steps_to_first_cmd']})")
            print(f"• Retry Streaks:        {transcript_report['retry_streaks']} streaks ({transcript_report['wasted_retry_calls']} wasted retry calls)")
        return 0

    else:
        parser.print_help()
        return 0


if __name__ == "__main__":
    sys.exit(main())
