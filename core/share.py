"""Shareable savings card and fact export."""

import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any

# Ensure repo root is in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.facts_engine import DEFAULT_USD_PER_MTOK, FactsEngine


def _format_token_count(tokens: int) -> str:
    if abs(tokens) >= 1_000_000:
        return f"{tokens / 1_000_000:.2f}M"
    elif abs(tokens) >= 1_000:
        return f"{tokens / 1_000:.1f}K"
    return str(tokens)


def handle_share(engine: FactsEngine, args: Any) -> int:
    summary = engine.get_metrics_summary(usd_per_mtok=getattr(args, "rate", DEFAULT_USD_PER_MTOK))
    net_tokens = summary.get("attributed_net_saved_tokens", summary["net_saved_tokens"])
    tok_fmt = _format_token_count(net_tokens)
    dollars = summary["saved_usd"]
    loop_stops = summary["loop_interventions"]
    first_try = summary["first_try_success_rate"]

    badge_label = f"Tokens%20Saved%20with%20Centsible-{tok_fmt}-brightgreen"
    badge_md = f"[![Tokens Saved with Centsible](https://img.shields.io/badge/{badge_label}?style=flat-square&logo=coin)](https://github.com/ntrieuvi97/centsible)"

    tweet_text = (
        f"I saved {tok_fmt} tokens (${dollars:.2f}) on AI coding agents with Centsible 🪙⚡\n"
        f"Zero dependencies, active loop guard, and continuous repo memory.\n\n"
        f"Check it out:"
    )
    tweet_url = f"https://twitter.com/intent/tweet?text={urllib.parse.quote(tweet_text)}&url={urllib.parse.quote('https://github.com/ntrieuvi97/centsible')}"

    card = [
        "┌────────────────────────────────────────────────────────┐",
        "│  🪙 Centsible Token & Cost Savings Report              │",
        "├────────────────────────────────────────────────────────┤",
        f"│  💰 Net Saved:     {tok_fmt:>8} tokens (${dollars:.2f}){'':<18}│",
        f"│  🛑 Loop Stops:    {loop_stops:>8} runaway loops halted{'':<11}│",
        f"│  🎯 1st-Try Rate:  {first_try:>7.1f}% tool success{'':<19}│",
        "├────────────────────────────────────────────────────────┤",
        "│  ⚡ Experience-driven token firewall for AI agents    │",
        "│  🔗 https://github.com/ntrieuvi97/centsible            │",
        "└────────────────────────────────────────────────────────┘",
    ]
    card_str = "\n".join(card)

    if getattr(args, "json", False):
        data = {
            "net_saved_tokens": net_tokens,
            "net_saved_formatted": tok_fmt,
            "saved_usd": dollars,
            "loop_interventions": loop_stops,
            "first_try_success_rate": first_try,
            "badge_markdown": badge_md,
            "share_card": card_str,
            "twitter_share_url": tweet_url,
        }
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0

    if getattr(args, "badge", False):
        print(badge_md)
        return 0

    if getattr(args, "card", False):
        print(card_str)
        return 0

    print(card_str)
    print("\n📌 Markdown Badge for your README:")
    print(badge_md)
    print("\n🐦 One-Click Share on X (Twitter):")
    print(tweet_url)
    return 0


def handle_export(engine: FactsEngine, args: Any) -> int:
    facts = engine.list_facts()
    target = getattr(args, "target", "cursor")
    stdout = getattr(args, "stdout", False)
    output_path = getattr(args, "output", None)

    if target == "json":
        content = json.dumps(facts, indent=2, ensure_ascii=False)
        default_out = "centsible-facts.json"
    elif target == "markdown":
        lines = [
            "# Repository Micro-Facts & Gotchas",
            "",
            "> Managed with [centsible](https://github.com/ntrieuvi97/centsible)",
            "",
        ]
        for f in facts:
            lines.append(f"- **[{f.get('id', 'fact')}]** `{f.get('pattern')}`: {f.get('fact')}")
        content = "\n".join(lines) + "\n"
        default_out = "CENTSIBLE_FACTS.md"
    elif target == "aider":
        lines = [
            "# Repository Conventions & Architectural Gotchas",
            "# Automatically synced via centsible (https://github.com/ntrieuvi97/centsible)",
            "",
        ]
        for f in facts:
            lines.append(f"- {f.get('fact')} (Context trigger: {f.get('pattern')})")
        content = "\n".join(lines) + "\n"
        default_out = ".aider.conventions.md"
    else:  # cursor
        lines = [
            "---",
            "description: Repository micro-facts and architectural gotchas managed by Centsible",
            "globs: *",
            "alwaysApply: true",
            "---",
            "",
            "# Repository Micro-Facts & Architectural Gotchas",
            "",
            "> Exported by [centsible](https://github.com/ntrieuvi97/centsible) — do not edit manually.",
            "",
        ]
        for f in facts:
            lines.append(f"- **{f.get('fact')}** (When encountering: `{f.get('pattern')}`)")
        content = "\n".join(lines) + "\n"
        if Path(".cursor/rules").is_dir():
            default_out = ".cursor/rules/centsible-facts.mdc"
        else:
            default_out = ".cursorrules"

    if stdout:
        sys.stdout.write(content)
        return 0

    dest = Path(output_path if output_path else default_out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(content, encoding="utf-8")
    print(f"✓ Exported {len(facts)} micro-fact(s) to {dest}")
    return 0
