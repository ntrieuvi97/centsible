"""Handlers for the non-hook sub-commands: config, facts, status, harvest, events, stats."""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

# Ensure repo root is in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.transcript import _parse_ts, analyze_transcript, claude_project_dir, compare_baseline
from core.event_log import read_events, summarize_events
from core.facts_engine import FactsEngine, derive_summary, get_global_facts_path
from core.loop_guard import (AUTO_LEARN_ENV, CONFIG_KEYS, INJECT_CHOICES, INJECT_ENV, MODE_ENV, MODES, REFLECT_ENV, get_config_path, load_config, normalize_reflect_mode, resolve_inject_mode, resolve_mode, resolve_reflect, resolve_threshold, save_config)


_FACT_KEYS = ("fact_hits", "attributed_hits", "injected_tokens", "saved_discovery_tokens", "auto_learned_facts")


def _summary_all_projects(rate: float) -> Dict[str, Any]:
    """Sum every project's summary, then fold in the legacy global bucket (~/.centsible/metrics.json)."""
    gpath = get_global_facts_path()
    dirs = sorted(d for d in (gpath.parent / "projects").glob("*") if d.is_dir())
    total = dict.fromkeys(_SUM_KEYS, 0)
    for d in dirs:
        for k, v in FactsEngine(d / "facts.jsonl").get_metrics_summary(usd_per_mtok=rate).items():
            if k in total:
                total[k] += v
    for k, v in FactsEngine(gpath).get_metrics_summary(usd_per_mtok=rate).items():
        if k in _FACT_KEYS:
            total[k] = max(total[k], v)  # shortcut: assumes global hits are a superset of migrated ones
        elif k in total:
            total[k] += v
    return derive_summary({**total, "projects": len(dirs)}, rate)


_SUM_KEYS = (
    "prompt_injections", "fact_hits", "attributed_hits", "tool_hint_hits", "injected_tokens", "saved_discovery_tokens",
    "loop_interventions", "saved_loop_tokens", "measured_loop_tokens", "tool_calls_total", "tool_calls_failed",
    "first_try_successes", "command_calls_total", "command_calls_failed", "first_try_cmd_successes",
    "auto_learned_facts", "total_facts",
)


def cmd_config(args) -> int:
    cfg = load_config()
    if args.config_action == "mode" and args.name:
        cfg["mode"] = args.name
        save_config(cfg)
    elif args.config_action == "reflect" and getattr(args, "state", None):
        norm = normalize_reflect_mode(args.state)
        if norm:
            cfg["reflect"] = norm
            save_config(cfg)
    elif args.config_action == "inject" and getattr(args, "mode", None):
        if args.mode in INJECT_CHOICES:
            cfg["inject_mode"] = args.mode
            save_config(cfg)
    elif args.config_action == "get":
        if args.key == "mode":
            print(resolve_mode())
        elif args.key == "reflect":
            print(resolve_reflect())
        elif args.key in ("inject", "inject_mode"):
            print(resolve_inject_mode())
        else:
            print(resolve_threshold(args.key))
        return 0
    elif args.config_action == "set":
        if args.value < 1:
            print("✗ value must be >= 1")
            return 1
        cfg[args.key] = args.value
        save_config(cfg)
    elif args.config_action == "unset":
        cfg.pop(args.key, None)
        if args.key == "reflect":
            cfg.pop("auto_learn_nudge", None)
        if args.key in ("inject", "inject_mode"):
            cfg.pop("inject_mode", None)
        save_config(cfg)

    note_mode = f" (overridden by env {MODE_ENV})" if os.environ.get(MODE_ENV) in MODES else ""
    print(f"mode = {resolve_mode()}{note_mode}")
    env_ref_name = None
    for k in (REFLECT_ENV, AUTO_LEARN_ENV):
        v = os.environ.get(k)
        if v and v.strip():
            env_ref_name = k
            break
    note_reflect = f" (overridden by env {env_ref_name})" if env_ref_name else ""
    print(f"reflect = {resolve_reflect()}{note_reflect}")
    note_inject = f" (overridden by env {INJECT_ENV})" if os.environ.get(INJECT_ENV) in INJECT_CHOICES else ""
    print(f"inject = {resolve_inject_mode()}{note_inject}")
    for key in sorted(CONFIG_KEYS):
        env_name = CONFIG_KEYS[key]
        note = f" (overridden by env {env_name})" if os.environ.get(env_name) else ""
        print(f"{key} = {resolve_threshold(key)}{note}")
    print(f"Config file: {get_config_path()}")
    return 0


def cmd_facts(args, engine, facts_parser) -> int:
    if args.facts_action == "list":
        facts = engine.list_facts(category=args.category)
        if not facts:
            print("No micro-facts stored yet. Add one with 'centsible facts add --pattern ... --fact ...'")
            return 0
        print(f"Stored Micro-Facts ({len(facts)}):")
        print(f"Path: {engine.storage_path}")
        if engine.global_path:
            print(f"Global: {engine.global_path}")
        print()
        for f in facts:
            tag = " [global]" if f.get("_scope") == "global" else ""
            print(f"• [{f.get('id')}]{tag} (category: {f.get('category', 'general')}, hits: {f.get('hits', 0)})")
            print(f"  Pattern: {f.get('pattern')}")
            print(f"  Fact:    {f.get('fact')}\n")
        return 0

    elif args.facts_action == "add":
        try:
            entry = engine.add_fact(
                pattern=args.pattern,
                fact=args.fact,
                category=args.category,
                fact_id=args.id,
                scope="global" if args.is_global else "project",
                force=args.force,
            )
        except ValueError as e:
            print(f"✗ Not saved: {e}", file=sys.stderr)
            return 2
        print(f"✓ Added micro-fact [{entry['id']}]: '{entry['fact']}' (Trigger: {entry['pattern']})")
        return 0

    elif args.facts_action == "remove":
        removed = engine.remove_fact(args.target, scope="global" if args.is_global else "project")
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

    elif args.facts_action == "prune":
        scope = "global" if getattr(args, "is_global", False) else "project"
        pruned, kept = engine.prune_facts(
            min_hits=args.min_hits,
            max_age_days=args.days,
            dry_run=args.dry_run,
            scope=scope,
        )
        if not pruned:
            print(f"✓ No stale or zero-utility facts to prune (checked {len(kept)} active facts).")
            return 0
        action_word = "Would prune" if args.dry_run else "Pruned"
        print(f"=== Centsible Facts Pruning 🪙 ===")
        print(f"• {action_word} {len(pruned)} fact(s) ({len(kept)} remaining):")
        for f in pruned:
            print(f"  - [{f.get('id')}] ({f.get('pattern')}) -> {f.get('fact')} [Reason: {f.get('prune_reason')}]")
        if args.dry_run:
            print("\nRun without --dry-run to commit deletion.")
        return 0

    else:
        facts_parser.print_help()
        return 1


def cmd_status(args, engine) -> int:
    summary = engine.get_metrics_summary()
    print("=== Centsible Status 🪙 ===")
    print(f"• Facts Storage:  {engine.storage_path}")
    print(f"• Total Facts:    {summary['total_facts']}")
    hit_usage = f" (Attributed: {summary.get('attributed_hits', 0)}, {summary.get('attribution_rate', 0.0):.1f}%)" if summary['fact_hits'] > 0 else ""
    print(f"• Total Hits:     {summary['fact_hits']}{hit_usage}")
    measured_trace = f" (~{summary.get('saved_loop_tokens', 0):,} trace tokens saved)" if summary['loop_interventions'] > 0 else ""
    print(f"• Loop Stops:     {summary['loop_interventions']}{measured_trace}")
    if summary.get("attributed_hits", 0) > 0 or summary.get("fact_hits", 0) == 0:
        saved_str = f"~{summary.get('attributed_net_saved_tokens', summary['net_saved_tokens']):,} tokens [Verified]"
    else:
        saved_str = f"~{summary.get('attributed_net_saved_tokens', 0):,} tokens [Verified: 0 applied, overhead -{summary['injected_tokens']:,}]"
    print(f"• Tokens Saved:   {saved_str} ({summary['saved_cents']:.2f}¢ / ${summary['saved_usd']:.4f})")
    cmd_rate_str = f" | {summary.get('first_try_cmd_rate', 100.0):.1f}% cmd" if summary.get("command_calls_total", 0) > 0 else ""
    print(f"• 1st-Try Rate:   {summary['first_try_success_rate']:.1f}% tools{cmd_rate_str} ({summary['tool_calls_total']} calls)")
    print(f"• Loop Guard:     Enabled (mode {resolve_mode()}: {resolve_threshold('max_identical_calls')} identical, {resolve_threshold('max_consecutive_errors')} errors)")
    print("• Status:         Active")
    if summary['total_facts'] == 0:
        print("• Tip:            No facts found. Run '/centsible-harvest' or 'centsible harvest' to discover golden facts.")
    return 0


def cmd_harvest(args, engine) -> int:
    summary = engine.get_metrics_summary()
    if args.prompt:
        prompt_text = (
            "You are the Centsible Fact Harvester 🪙.\n"
            "Systematically explore this repository and capture 3 to 5 high-signal Golden Micro-Facts "
            "to maximize future token efficiency and prevent agent trial-and-error loops.\n\n"
            "Zero-Waste Golden Rules:\n"
            "- ZERO TRIVIAL FACTS: Never save standard commands ('npm test', 'git status', 'python main.py'). "
            "If this repository strictly adheres to standard framework conventions without quirks or hidden flags, output ZERO facts. "
            "Having 0 facts is strictly superior to creating noisy facts.\n"
            "- CONTEXTUALIZED REGEX: Patterns must pair the tool with a symptom/flag, e.g. 'pytest.{0,40}(fail|flag)' or specific file paths.\n\n"
            "Execution Procedure:\n"
            "1. Inspect lockfiles and package configs for non-standard runners or monorepo filters.\n"
            "2. Inspect CI/CD workflows for required custom flags, environment variables, or gotchas.\n"
            "3. If and only if unique quirks exist, distill 1 imperative sentence (<= 120 chars).\n"
            "4. Save via: centsible facts add --pattern \"<pattern>\" --fact \"<fact>\" --category \"<category>\"\n"
            "5. Run 'centsible facts list' and summarize the saved facts."
        )
        print(prompt_text)
        return 0

    print("=== Centsible Fact Harvester 🪙 ===")
    print(f"• Storage Path:   {engine.storage_path}")
    print(f"• Active Facts:   {summary['total_facts']}")
    if summary["total_facts"] > 0 and not args.force:
        print("\nRepo already has active micro-facts. Use 'centsible facts list' to inspect.")
        print("To rerun discovery, pass '--force' or run '/centsible-harvest' in your agent.")
        return 0

    print("\nTo harvest 5-8 golden architectural facts for this repo:")
    print("  1. In Claude Code:    Type '/centsible-harvest'")
    print("  2. In Antigravity:    Ask your agent: \"Run centsible harvest to discover golden facts\"")
    print("  3. View Raw Prompt:   Run 'centsible harvest --prompt'")
    return 0


def cmd_events(args, engine) -> int:
    from datetime import timedelta
    evs = read_events(since=datetime.utcnow() - timedelta(days=args.days))
    if args.agent:
        evs = [e for e in evs if e.get("agent") == args.agent]
    if not args.all:
        evs = [e for e in evs if e.get("project") == Path.cwd().resolve().name]
    summ = summarize_events(evs)
    if args.json:
        print(json.dumps(summ, ensure_ascii=False, indent=2))
        return 0
    print(f"Events in last {args.days}d: {summ['total']}")
    for agent, counts in sorted(summ["agents"].items()):
        print(f"• {agent}: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    dead = [(fid, f) for fid, f in summ["facts"].items() if f["shown"] >= 20 and f["applied"] == 0 and not fid.startswith("builtin_")]  # builtin guards warn, they are never "applied"
    for fid, f in sorted(summ["facts"].items(), key=lambda kv: -kv[1]["shown"])[:10]:
        print(f"  fact {fid}: shown={f['shown']} applied={f['applied']}")
    if dead:
        print("⚠ Shown >=20 times, never applied: " + ", ".join(fid for fid, _ in dead))
    return 0


def cmd_stats(args, engine) -> int:
    if args.all:
        summary = _summary_all_projects(args.rate)
        args.no_baseline = True
    else:
        summary = engine.get_metrics_summary(usd_per_mtok=args.rate)
    transcript_report = analyze_transcript(Path(args.transcript)) if args.transcript else None
    baseline_report = None
    auto = args.baseline == "auto"
    if args.baseline and not args.no_baseline:
        since = _parse_ts(args.since) if args.since else min(
            filter(None, (_parse_ts(f.get("created_at")) for f in engine.list_facts())), default=None
        )
        baseline_dir = claude_project_dir(Path.cwd()) if auto else Path(args.baseline).expanduser()
        if auto and (since is None or not baseline_dir.is_dir()):
            pass  # default mode: nothing to compare, stay silent
        elif since is None:
            print("✗ --baseline needs --since (invalid, or no facts with created_at yet).", file=sys.stderr)
            return 1
        elif not baseline_dir.is_dir():
            print(f"✗ --baseline dir not found: {baseline_dir}", file=sys.stderr)
            return 1
        else:
            baseline_report = compare_baseline(baseline_dir, since)
            if auto and not (baseline_report["before"]["sessions"] or baseline_report["after"]["sessions"]):
                baseline_report = None
    if args.json:
        payload_out: Dict[str, Any] = {"metrics": summary}
        if transcript_report:
            payload_out["transcript"] = transcript_report
        if baseline_report:
            payload_out["baseline"] = baseline_report
        print(json.dumps(payload_out, ensure_ascii=False, indent=2))
        return 0

    print("=== Centsible Efficiency & ROI Report 🪙⚡ ===")
    print(f"• Storage Path:         {'all projects (' + str(summary['projects']) + ')' if args.all else engine.storage_path}")
    print(f"• Active Micro-Facts:   {summary['total_facts']} (Auto-learned: {summary['auto_learned_facts']})")
    print(f"• Total Fact Hits:      {summary['fact_hits']} (Prompt: {summary['prompt_injections']}, Pre-Tool Hints: {summary['tool_hint_hits']})")
    if summary.get("fact_hits", 0) > 0:
        print(f"• Fact Attribution:     {summary.get('attributed_hits', 0)} verified applied ({summary.get('attribution_rate', 0.0):.1f}% usage rate)")
    print(f"• Loop Interventions:   {summary['loop_interventions']} halted runaway loops (~{summary.get('saved_loop_tokens', 0):,} trace tokens saved)")
    cmd_info = f" (commands: {summary.get('first_try_cmd_rate', 100.0):.1f}% 1st-try)" if summary.get("command_calls_total", 0) > 0 else ""
    print(f"• Tool Execution:       {summary['tool_calls_total']} calls | {summary['first_try_success_rate']:.1f}% 1st-try success{cmd_info} | {summary['tool_error_rate']:.1f}% error rate")
    print("--- Token & Cost Accounting ---")
    print(f"• Discovery Saved:      +{summary['saved_discovery_tokens']:,} tokens [Heuristic] | +{summary.get('attributed_saved_tokens', 0):,} tokens [Attributed]")
    print(f"• Loop Guard Saved:     +{summary['saved_loop_tokens']:,} tokens [Trace-measured]")
    print(f"• Ephemeral Overhead:   -{summary['injected_tokens']:,} tokens")
    print(f"• Attributed Net Saved: {summary.get('attributed_net_saved_tokens', 0):,} tokens [Verified]")
    print(f"• Net Tokens Saved:     {summary['net_saved_tokens']:,} tokens (ROI: {summary['roi_multiplier']}x) [Heuristic, unverified upper bound]")
    print(f"• Estimated Savings:    {summary['saved_cents']:.2f}¢ (${summary['saved_usd']:.4f} @ ${args.rate:.2f}/1M tok, heuristic)")
    if not args.all and not summary["total_facts"] and not summary["tool_calls_total"]:
        print("ℹ No data for this directory (stats are per-project). cd into a repo, or run 'centsible stats --all'.")

    if transcript_report:
        print("--- Session Transcript Analysis ---")
        print(f"• Transcript:           {transcript_report['path']}")
        print(f"• Steps / User Turns:   {transcript_report['total_steps']} steps / {transcript_report['user_turns']} turns")
        print(f"• Tool / Cmd Calls:     {transcript_report['tool_calls']} tools ({transcript_report['command_calls']} commands, {transcript_report['failed_calls']} failed)")
        print(f"• 1st-Try Cmd Success:  {transcript_report['first_try_cmd_rate']:.1f}% (Discovery steps before 1st cmd: {transcript_report['discovery_steps_to_first_cmd']})")
        wasted_tok_str = f" / ~{transcript_report.get('wasted_retry_tokens', 0):,} error tokens" if transcript_report.get('wasted_retry_tokens', 0) > 0 else ""
        print(f"• Retry Streaks:        {transcript_report['retry_streaks']} streaks ({transcript_report['wasted_retry_calls']} wasted retry calls{wasted_tok_str})")
        print(f"• Before 1st Edit:      {transcript_report['calls_before_first_edit']} calls / ~{transcript_report['tokens_before_first_edit']:,} output tokens ({transcript_report['edit_calls']} edit calls)")
    if baseline_report:
        b, a = baseline_report["before"], baseline_report["after"]
        print("--- Measured Exploration Cost (before vs after facts) ---")
        print(f"• Split at:             {baseline_report['since']}")
        print(f"• Before:               {b['sessions']} sessions | median {b['median_calls']} calls / {b['median_tokens']} tokens before 1st edit")
        print(f"• After:                {a['sessions']} sessions | median {a['median_calls']} calls / {a['median_tokens']} tokens before 1st edit")
        if baseline_report["enough_data"]:
            print(f"• Measured Saving:      {baseline_report['saved_calls_per_session']:+} calls, {baseline_report['saved_tokens_per_session']:+} tokens per session (negative = worse)")
        else:
            print(f"• Measured Saving:      not enough data (need >= {baseline_report['min_sessions']} edit-sessions in each group)")
    return 0
