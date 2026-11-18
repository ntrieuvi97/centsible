# centsible 🪙

**Repo memory and loop guard for AI coding agents, with savings reported as *verified* vs *estimated*.** Centsible stores one-line repo facts, injects each only when the situation matches, stops runaway retry loops, and tells you how many tokens it actually saved. Standard library only.

[![CI](https://github.com/ntrieuvi97/centsible/actions/workflows/ci.yml/badge.svg)](https://github.com/ntrieuvi97/centsible/actions/workflows/ci.yml)
[![Version: 0.10.0](https://img.shields.io/badge/Version-0.10.0-orange.svg)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.9+](https://img.shields.io/badge/python-3.9%2B-green.svg)](https://www.python.org/)
[![Dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](#)
[![Works with: Claude Code | Antigravity | OpenClaw | Hermes](https://img.shields.io/badge/works%20with-Claude%20Code%20%7C%20Antigravity%20%7C%20OpenClaw%20%7C%20Hermes-purple.svg)](#quickstart)

---

## The problem

Every session, an agent rediscovers the same things: which command runs the tests, which flag is mandatory, which file must never be edited by hand. It also retries failing commands far longer than it should. That is wasted time and tokens.

## What Centsible does differently

| | Typical "memory" add-on | Centsible |
|---|---|---|
| **When a fact appears** | Loaded up front, always. | Injected for that turn only, when its trigger pattern matches the prompt or the tool call. |
| **What can be saved** | Anything. | One sentence, 240 characters max. Lone short keywords (`pytest`) and generic-word patterns are rejected because they would fire on unrelated prompts. |
| **Who writes facts** | You. | You, or the agent itself: it saves a hard-won gotcha in the same turn (no extra turn by default). |
| **Runaway loops** | Not handled. | Loop guard halts repeated errors and identical tool calls, with `ultra`, `normal` and `weak` presets. |
| **Savings claims** | A single headline number. | Separate lines for *trace-measured*, *attributed (verified)* and *heuristic* savings, so you can see how much is evidence and how much is an estimate. |

## Measured on real usage

The numbers below come from `centsible stats` on one actively used repository (16 curated facts, 12,447 tool calls over several weeks). They are from real sessions, not a benchmark, and the "before/after" split is not a controlled experiment.

| Metric | Result | How it is measured |
|---|---|---|
| Tool calls observed | 12,447 | Transcript analysis |
| First-try success | 94.6% overall, 96.8% for shell commands | Transcript analysis |
| Runaway loops halted | 60 (about 120,000 trace tokens avoided) | Trace-measured |
| Net tokens saved, **verified** | **312,785** | Attributed: only facts the agent demonstrably applied |
| Facts verified as applied | 199 of 2,111 hits (9.4%) | Attribution |
| Exploration before first edit | median 31.0 calls / 22,109 tokens before facts, 28.0 calls / 18,138 tokens after (66 vs 20 sessions) | Measured split at the date facts were introduced |
| Net tokens saved, heuristic | 3,180,785 (upper bound) | Heuristic, unverified |

How to read this: the loop guard produces measured savings, and fact injection produces verified savings of a few hundred thousand tokens in this repo. The before/after split suggests roughly 4,000 fewer tokens (about 18%) of exploration per session, but the "after" group is only 20 sessions and other things changed over the same period. The large heuristic figure is an upper bound and should not be quoted as the benefit.

Results depend on fact quality. Across a set of 9 mixed repositories (3,425 tool calls), where only 4 facts were active, 2.6% of fact hits were verified as applied and verified savings were about 5,000 tokens. We have not isolated the cause, but the likely lever is having well-targeted facts for the repo you work in every day. Run `centsible stats` on your own sessions to get yours.

## Quickstart

### Claude Code

```text
/plugin marketplace add ntrieuvi97/centsible
/plugin install centsible@centsible
```

Then use `/centsible-stats`, `/centsible-status`, `/centsible-harvest` or `/centsible-config` in chat.

### One-line installer (Linux and macOS)

```bash
curl -fsSL https://raw.githubusercontent.com/ntrieuvi97/centsible/main/install.sh | bash
source ~/.zshrc   # or ~/.bashrc
centsible stats
```

### Google Antigravity

```bash
git clone https://github.com/ntrieuvi97/centsible.git ~/.gemini/config/plugins/centsible
~/.gemini/config/plugins/centsible/install.sh
```

### Python (any OS, including Windows)

```bash
pip install git+https://github.com/ntrieuvi97/centsible.git
centsible stats
```

### Hermes Agent

```bash
centsible install --hermes   # prints a `hooks:` block; edits nothing
```

Merge the printed block into your profile's `config.yaml` (`$HERMES_HOME/config.yaml`), then approve the hooks once (start `hermes` and accept the prompt; `hermes hooks doctor` checks them). Centsible uses four Hermes shell hooks: `pre_llm_call` (fact injection), `pre_tool_call` (loop guard, budget, tool hints), `post_tool_call` (telemetry and recovery learning) and `pre_verify` (one save-the-gotcha nudge on turns that edited code).

Two differences from Claude Code: Hermes cannot attach context to a tool call it lets through, so a one-time fact hint blocks that one call and the agent retries with the hint as the tool result. Facts are also resolved from the directory Hermes runs in, so start Hermes from the repo.

### First five minutes

```bash
centsible harvest                 # scan a fresh repo and propose up to 5 starting facts (0 if nothing is unusual)
centsible facts add --pattern "pytest.{0,40}(fail|error)" --fact "Run with 'pytest tests/ -q --tb=short'."
centsible facts test "why does pytest fail?"   # preview which facts would fire
centsible stats                   # see what it saved
```

## CLI cheat sheet

```bash
centsible status
centsible stats [--all] [--rate 3.0] [--transcript session.jsonl] [--baseline DIR] [--since 2026-11-04] [--no-baseline]
centsible events [--days 7] [--agent main] [--json]    # hint/block/apply counts; flags facts shown >=20x but never applied

centsible facts add --pattern "<regex>" --fact "<one line>" [--category gotcha] [--global]
centsible facts list
centsible facts test "<prompt>"
centsible facts prune [--days 30] [--min-hits 20] [--dry-run]   # remove stale or zero-utility facts
centsible facts verify [--root DIR]                  # files named in facts still exist? exit 1 if a path is stale
centsible facts replay [--days 30] [--transcripts DIR]   # replay past sessions: which facts never fire, fire too often, or are redundant
centsible facts tune [--days 30] [--transcripts DIR] [--apply]   # suggest remove / narrow / merge from hits, applies and past sessions
centsible facts remove <id_or_pattern>

centsible config mode ultra            # loop guard preset: ultra | normal | weak
centsible config reflect inline        # inline (default, zero extra turns) | hook | off
centsible config set max_consecutive_errors 4
centsible config set session_budget_tokens 2000000   # per-session budget: warn at 80%, then block tool calls
centsible config set session_budget_calls 300
centsible config list
```

Writing a good pattern: anchor on an identifier (`deploy_staging`, `run-e2e`, `a/b.py`), a tool plus a symptom (`pytest.{0,40}(fail|error)`), or an action plus object plus context. `--force` overrides the check for deliberately broad patterns.

`facts tune` only changes facts with `--apply`, and only the suggestions marked `[auto]`: removing facts that never fired or are redundant, removing exact duplicates, and dropping a `a|b` branch that fired in 3+ past sessions where the fact was never applied while another branch was. Everything else is printed as `[manual]`.

Session budgets are off by default. Token usage is measured from the transcript where the host provides one (Claude Code; cached reads count at 10%). On other hosts it is estimated from tool input and output only, which undercounts, so prefer `session_budget_calls` there. Once a limit is reached every further tool call in that session is blocked until you raise or unset it. `budget_warn` / `budget_stop` appear in `centsible events`.

Per-fact counters (`hits`, `attributed_hits`, ...) are stored beside the facts in `facts.stats.json`, so the facts file only changes when you change a fact and is safe to commit. Add `facts.stats.json` and `metrics.json` to `.gitignore`. Counters from older versions move over automatically on the next write.

Facts live in the project's `.agents/` or `.claude/` directory when present, otherwise in `~/.centsible/projects/<repo>-<hash>/` (scoped per repo, never shared). Facts added with `--global` live in `~/.centsible/facts.jsonl`; a project fact with the same id or pattern wins. Override the root with `CENTSIBLE_HOME`.

<details>
<summary>Example <code>centsible stats</code> output</summary>

```text
=== Centsible Efficiency & ROI Report 🪙 ===
• Active Micro-Facts:   16 (Auto-learned: 1)
• Total Fact Hits:      2111 (Prompt: 433, Pre-Tool Hints: 249)
• Fact Attribution:     199 verified applied (9.4% usage rate)
• Loop Interventions:   60 halted runaway loops (~120,000 trace tokens saved)
• Tool Execution:       12447 calls | 94.6% 1st-try success | 1.4% error rate
--- Token & Cost Accounting ---
• Discovery Saved:      +3,166,500 tokens [Heuristic] | +298,500 tokens [Attributed]
• Loop Guard Saved:     +120,000 tokens [Trace-measured]
• Ephemeral Overhead:   -105,715 tokens
• Attributed Net Saved: 312,785 tokens [Verified]
• Net Tokens Saved:     3,180,785 tokens (ROI: 31.1x) [Heuristic, unverified upper bound]
--- Measured Exploration Cost (before vs after facts) ---
• Before:               66 sessions | median 31.0 calls / 22108.5 tokens before 1st edit
• After:                20 sessions | median 28.0 calls / 18137.5 tokens before 1st edit
• Measured Saving:      +3.0 calls, +3971.0 tokens per session (negative = worse)
```
</details>

## How it works

```text
1. User prompt     ──► [Prompt hook]   ──► injects matching facts for this turn only
2. Tool execution  ──► [PreTool hook]  ──► loop guard, CLI flag and file-gotcha checks
3. Tool outcome    ──► [PostTool hook] ──► telemetry, learns from successful recoveries
4. Session end     ──► [Stop hook]     ──► one-shot reflection nudges the agent to save gotchas
```

## Languages

"Save this rule" detection (e.g. "never edit `a.py`") ships with English, Vietnamese, Spanish, Portuguese, French, German, Russian, Indonesian, Chinese, Japanese and Korean phrases in `core/triggers.json`. To add or extend a language without a release, put a file with the same shape in `~/.centsible/triggers.json` (or `$CENTSIBLE_HOME/triggers.json`):

```json
{ "nl": { "correction": ["gebruik\\s+nooit", "zorg\\s+voor"], "question_start": ["waarom", "hoe"] } }
```

Fact patterns themselves are plain regexes and work in any language.

## Limits

- Facts only help when their patterns match. Even in the best case above, 9.4% of hits were verified as applied, so most injections are not used. That is why `facts prune` and `centsible events` exist.
- Savings figures labelled *Heuristic* are estimates. Treat *Verified* and *Trace-measured* as the numbers you can rely on.
- Transcript analysis supports the agents listed above; other agents may need `--transcript`.

## Develop

```bash
git clone https://github.com/ntrieuvi97/centsible.git && cd centsible
python3 -m pip install -e . pytest
python3 -m pytest -q          # or: python3 -m unittest discover tests
```

See [CONTRIBUTING.md](CONTRIBUTING.md). Related project: [Thauthi](https://github.com/ntrieuvi97/thauthi), runtime-aware tooling for AI coding agents.

## License

MIT. See [LICENSE](LICENSE).
