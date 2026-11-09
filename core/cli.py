#!/usr/bin/env python3
"""
CLI entrypoint: parses args and routes to core.hooks / core.commands.
"""

import sys
from pathlib import Path

# Ensure repo root is in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.cli_parser import build_parser
from core.commands import cmd_config, cmd_events, cmd_facts, cmd_harvest, cmd_stats, cmd_status
from core.hooks import (
    _engine,
    handle_hook_claude_post_tool,
    handle_hook_claude_pre_tool,
    handle_hook_claude_prompt,
    handle_hook_claude_stop,
    handle_hook_post_tool,
    handle_hook_pre_invocation,
    handle_hook_pre_tool,
    handle_hook_stop,
)
from core.install import ensure_cli_symlink, handle_install
from core.share import handle_export, handle_share
from core.transcript import analyze_transcript, claude_project_dir, compare_baseline  # noqa: F401  re-exported for tests


def main() -> int:
    parser, hook_parser, facts_parser = build_parser()
    args = parser.parse_args()

    if args.command == "install":
        return handle_install(dry_run=args.dry_run)

    # Route hook commands
    if args.command == "hook":
        ensure_cli_symlink()
        if args.hook_type == "pre-invocation":
            handle_hook_pre_invocation()
            return 0
        elif args.hook_type == "pre-tool":
            handle_hook_pre_tool()
            return 0
        elif args.hook_type == "post-tool":
            handle_hook_post_tool()
            return 0
        elif args.hook_type == "stop":
            handle_hook_stop()
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
        elif args.hook_type == "claude-post-tool-failure":
            handle_hook_claude_post_tool(failed=True)
            return 0
        elif args.hook_type == "claude-stop":
            handle_hook_claude_stop()
            return 0
        else:
            hook_parser.print_help()
            return 1

    if args.command == "config":
        return cmd_config(args)

    engine = _engine()
    handlers = {"facts": lambda: cmd_facts(args, engine, facts_parser), "status": lambda: cmd_status(args, engine),
                "harvest": lambda: cmd_harvest(args, engine), "events": lambda: cmd_events(args, engine),
                "stats": lambda: cmd_stats(args, engine), "share": lambda: handle_share(engine, args),
                "export": lambda: handle_export(engine, args)}
    if args.command in handlers:
        return handlers[args.command]()
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
