"""Argument parser for the centsible CLI."""

import argparse
import sys
from pathlib import Path

# Ensure repo root is in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import __version__
from core.facts_engine import DEFAULT_USD_PER_MTOK
from core.loop_guard import CONFIG_KEYS, INJECT_CHOICES, MODES, REFLECT_CHOICES


def build_parser():
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
    hook_sub.add_parser("stop", help="Antigravity Stop hook")

    # Claude Code hooks
    hook_sub.add_parser("claude-prompt", help="Claude Code UserPromptSubmit hook")
    hook_sub.add_parser("claude-pre-tool", help="Claude Code PreToolUse hook")
    hook_sub.add_parser("claude-post-tool", help="Claude Code PostToolUse hook")
    hook_sub.add_parser("claude-post-tool-failure", help="Claude Code PostToolUseFailure hook")
    hook_sub.add_parser("claude-stop", help="Claude Code Stop hook")

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
    facts_add.add_argument("--force", action="store_true", help="Skip the pattern-specificity and fact-length checks")
    facts_add.add_argument("--global", dest="is_global", action="store_true", help="Store in the global facts file shared by every repo")

    # facts remove
    facts_del = facts_sub.add_parser("remove", help="Remove a fact by ID or pattern")
    facts_del.add_argument("target", help="Fact ID or regex pattern to remove")
    facts_del.add_argument("--global", dest="is_global", action="store_true", help="Remove from the global facts file")

    # facts test
    facts_test = facts_sub.add_parser("test", help="Test fact matching against a prompt string")
    facts_test.add_argument("prompt", help="User input text to test")

    # facts prune
    facts_prune = facts_sub.add_parser("prune", help="Prune stale or zero-utility micro-facts")
    facts_prune.add_argument("--days", type=int, default=30, help="Prune facts older than N days with 0 hits (default: 30)")
    facts_prune.add_argument("--min-hits", type=int, default=20, help="Prune facts with >= N hits but 0% attribution (default: 20)")
    facts_prune.add_argument("--dry-run", action="store_true", help="Preview facts to prune without deleting")
    facts_prune.add_argument("--global", dest="is_global", action="store_true", help="Prune global facts file")

    # status command
    subparsers.add_parser("status", help="Show system status and facts count")

    # harvest command
    harvest_parser = subparsers.add_parser("harvest", help="Display or output agentic fact harvest instructions")
    harvest_parser.add_argument("--prompt", action="store_true", help="Output raw prompt suitable for an LLM agent")
    harvest_parser.add_argument("--force", action="store_true", help="Force harvest even if facts already exist")

    # stats command
    stats_parser = subparsers.add_parser("stats", help="Show token & cost savings metrics and session health")
    stats_parser.add_argument("--rate", type=float, default=DEFAULT_USD_PER_MTOK, help="Blended USD rate per 1M tokens (default: 3.0)")
    stats_parser.add_argument("--transcript", help="Optional path to a session transcript (.jsonl) to analyze")
    stats_parser.add_argument("--baseline", nargs="?", const="auto", default="auto", help="Dir of session transcripts (.jsonl) to compare exploration cost before vs after --since (default: this repo's Claude Code transcripts, skipped if none)")
    stats_parser.add_argument("--no-baseline", action="store_true", help="Skip the before/after exploration comparison")
    stats_parser.add_argument("--since", help="ISO date/datetime splitting before/after (default: earliest fact created_at)")
    stats_parser.add_argument("--all", action="store_true", help="Sum metrics across every project under ~/.centsible/projects (use when not inside a repo)")
    stats_parser.add_argument("--json", action="store_true", help="Output metrics as JSON")

    events_parser = subparsers.add_parser("events", help="Summarize the hint/block/apply event log per agent and per fact")
    events_parser.add_argument("--days", type=int, default=7, help="Look back this many days (default: 7)")
    events_parser.add_argument("--agent", help="Only this agent id (OpenClaw session keys only)")
    events_parser.add_argument("--all", action="store_true", help="Include every project and legacy untagged events (default: this directory's project only)")
    events_parser.add_argument("--json", action="store_true", help="Output the summary as JSON")

    config_parser = subparsers.add_parser("config", help="View or change loop guard thresholds")
    config_sub = config_parser.add_subparsers(dest="config_action")
    config_sub.add_parser("list", help="Show effective mode and thresholds")
    config_mode = config_sub.add_parser("mode", help="Show or set the loop guard mode preset")
    config_mode.add_argument("name", nargs="?", choices=sorted(MODES), help="ultra (strict), normal, weak (tolerant)")
    config_reflect = config_sub.add_parser("reflect", help="Show or set reflection mode (inline | hook | off)")
    config_reflect.add_argument("state", nargs="?", choices=REFLECT_CHOICES, help="inline (recommended: in-turn capture, zero extra turns), hook (blocking stop hook), off")
    config_inject = config_sub.add_parser("inject", help="Show or set fact injection strategy (smart | jit | eager | off)")
    config_inject.add_argument("mode", nargs="?", choices=INJECT_CHOICES, help="smart (Smart Hybrid: top domain facts in prompt, tool syntax in PreToolUse), jit (strict JIT), eager, off")
    config_get = config_sub.add_parser("get", help="Show one effective threshold or setting")
    config_get.add_argument("key", choices=sorted(CONFIG_KEYS) + ["mode", "reflect", "inject", "inject_mode"])
    config_set = config_sub.add_parser("set", help="Persist a threshold in config.json")
    config_set.add_argument("key", choices=sorted(CONFIG_KEYS))
    config_set.add_argument("value", type=int)
    config_unset = config_sub.add_parser("unset", help="Remove a setting or threshold (back to default)")
    config_unset.add_argument("key", choices=sorted(CONFIG_KEYS) + ["mode", "reflect", "inject", "inject_mode"])

    # install command
    install_parser = subparsers.add_parser("install", help="Install or symlink centsible CLI to user's PATH")
    install_parser.add_argument("--dry-run", action="store_true", help="Print actions without modifying files")

    # share command
    share_parser = subparsers.add_parser("share", help="Generate shareable savings card, badges, and social post")
    share_parser.add_argument("--card", action="store_true", help="Print ASCII share card only")
    share_parser.add_argument("--badge", action="store_true", help="Print Markdown shield badge only")
    share_parser.add_argument("--json", action="store_true", help="Output share metadata as JSON")
    share_parser.add_argument("--rate", type=float, default=DEFAULT_USD_PER_MTOK, help="Blended USD rate per 1M tokens (default: 3.0)")

    # export command
    export_parser = subparsers.add_parser("export", help="Export active micro-facts to Cursor, Aider, or Markdown")
    export_parser.add_argument("--target", "-t", choices=["cursor", "aider", "markdown", "json"], default="cursor", help="Target tool or format (default: cursor)")
    export_parser.add_argument("--output", "-o", help="Output file path (default depends on target)")
    export_parser.add_argument("--stdout", action="store_true", help="Print exported content to stdout instead of writing to disk")
    return parser, hook_parser, facts_parser
