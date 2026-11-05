# centsible 🪙⚡

> **Experience-driven cost and token optimization for AI coding agents (Google Antigravity, Claude Code & OpenClaw)**  
> *Make your AI agents work smart and save every token. Centsible = Sensible (smart reasoning) + Cent (cost efficiency).*

[![Version: 0.5.1](https://img.shields.io/badge/Version-0.5.1-orange.svg)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.8+](https://img.shields.io/badge/Python-3.8+-green.svg)](https://www.python.org/)
[![Zero Dependencies](https://img.shields.io/badge/Dependencies-Zero%20(Stdlib)-brightgreen.svg)](#)
[![Runtimes](https://img.shields.io/badge/Runtimes-Antigravity%20%7C%20Claude%20Code%20%7C%20OpenClaw-purple.svg)](#)

---

## ⚡ Why Centsible?

AI agents burn thousands of tokens rediscovering repo facts, repeating failing commands, and bloating system prompts. Centsible fixes this with zero external dependencies:

| Feature | What It Does | Benefit |
|---|---|---|
| 🧩 **Ephemeral Micro-Skills** | Injects 1-line repo facts only when prompted or when missing required flags/paths. | Zero context bloat, prompt cache stays 100% intact. |
| 🧠 **Autonomous Self-Learning** | Auto-learns from recoveries and user corrections; one-shot wrap-up reflection nudges agent to persist gotchas automatically. | Zero-maintenance continuous repo memory across sessions. |
| 🛡️ **Normalized Loop Guard** | Halts infinite retry loops and duplicate tool calls across AGY, Claude Code, and OpenClaw. | Saves context budget, prevents runaway costs. |
| 📊 **Token ROI Telemetry** | Real-time dollar & token savings metrics and offline session transcript diagnostics. | Immediate visibility into cost efficiency. |

---

## 🚀 Quickstart

### Claude Code
```text
/plugin marketplace add ntrieuvi97/centsible
/plugin install centsible@centsible
```

### Google Antigravity
```bash
git clone https://github.com/ntrieuvi97/centsible.git ~/.gemini/config/plugins/centsible
ln -sf ~/.gemini/config/plugins/centsible/bin/centsible ~/.local/bin/centsible
```

---

## 🛠️ CLI Cheat Sheet

```bash
# System status & ROI metrics
centsible status
centsible stats [--all] [--rate 3.0] [--transcript session.jsonl] [--baseline DIR] [--since 2026-11-04] [--no-baseline]   # baseline auto-detects this repo's transcripts

# Manage micro-facts and architectural gotchas
centsible facts add --pattern "pytest" --fact "Run with 'pytest tests/ -q --tb=short'"
centsible facts add --pattern "auth/" --fact "Never mutate token keys directly" --category gotcha
centsible facts add --pattern "rtk" --fact "Prefix shell commands with rtk" --global   # applies to every repo
centsible facts list
centsible facts test "How do I run tests?"
centsible facts remove <id_or_pattern>

# Agentic fact harvesting (initial scan on fresh repo)
centsible harvest [--prompt] [--force]
# Or in Claude Code chat: /centsible-harvest

# Configure loop guard & reflection modes (inline | hook | off)
centsible config mode ultra
centsible config reflect inline       # inline (default, zero extra turns) | hook | off
centsible config list
centsible config set max_consecutive_errors 4
```

<details>
<summary>📊 <b>View Example ROI & Transcript Analytics Output</b></summary>

```text
=== Centsible Efficiency & ROI Report 🪙⚡ ===
• Storage Path:         ~/.agents/facts.jsonl
• Active Micro-Facts:   3 (Auto-learned: 1)
• Total Fact Hits:      14 (Prompt: 11, Pre-Tool Hints: 3)
• Loop Interventions:   2 halted runaway loops
• Tool Execution:       17 calls | 94.1% 1st-try success | 5.9% error rate
--- Token & Cost Accounting ---
• Discovery Saved:      +21,000 tokens
• Loop Guard Saved:     +4,000 tokens
• Ephemeral Overhead:   -520 tokens
• Net Tokens Saved:     24,480 tokens (ROI: 48.1x)
• Estimated Savings:    7.34¢ ($0.0734 @ $3.00/1M tok)
```
</details>

Facts live in the project's `.agents/` or `.claude/` dir when present, otherwise in `~/.centsible/projects/<repo>-<hash>/` (scoped per repo, never shared). Override the root with `CENTSIBLE_HOME`. Facts added with `--global` live in `~/.centsible/facts.jsonl` and apply to every repo; a project fact with the same id or pattern wins.

---

## 🏗️ How It Works

```text
1. User Prompt     ──► [Prompt Hook]    ──► Injects matching fact ephemerally (turn-only)
2. Tool Execution  ──► [PreTool Hook]   ──► Loop Guard + validates CLI flags & file gotchas
3. Tool Outcome    ──► [PostTool Hook]  ──► Telemetry + auto-learns successful recoveries
4. Session End     ──► [Stop Hook]      ──► One-shot wrap-up reflection nudges agent to save hard-won gotchas
```

---

## 🧪 Testing

```bash
python3 -m pytest -q          # or, with zero deps: python3 -m unittest discover tests
```

---

## 📄 License

MIT. See [LICENSE](LICENSE).
