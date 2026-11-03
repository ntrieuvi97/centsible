# centsible 🪙⚡

> **Experience-driven cost and token optimization for AI coding agents (Antigravity & Claude Code)**  
> *Make your AI agents work smart and save every token. Centsible = Sensible (smart reasoning) + Cent (cost efficiency).*

[![Version: 0.2.0](https://img.shields.io/badge/Version-0.2.0-orange.svg)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.8+](https://img.shields.io/badge/Python-3.8+-green.svg)](https://www.python.org/)
[![Zero External Dependencies](https://img.shields.io/badge/Dependencies-Zero%20(Stdlib)-brightgreen.svg)](#)
[![Dual Runtime: AGY + Claude Code](https://img.shields.io/badge/Runtimes-Antigravity%20%7C%20Claude%20Code-purple.svg)](#)

---

## 🎯 The Problem

AI coding agents often burn thousands of unnecessary tokens due to 3 common patterns:
1. **Repetitive Discovery:** In every new conversation, the agent rediscovers basic repository facts (how tests are run, special command flags, path quirks) by grepping and guessing.
2. **Context Bloat:** Developers dump all hints into `AGENTS.md` / `CLAUDE.md`, which are paid for in **every single turn**, causing attention dilution (*lost in the middle*).
3. **Runaway Failure Loops:** Agents retry failing commands or repeated file edits 3–5 times before giving up, wasting context budget.

---

## 💡 How Centsible Solves It

`centsible` operates as a lightweight, zero-dependency controller sharing a single source of truth (`.agents/facts.jsonl` & `.agents/metrics.json`) across both **Google Antigravity** and **Claude Code**:

* 🧩 **Zero-Bloat Micro-Skills (Prompt + Pre-Tool Catch):**
  Learns concise, 1-line repository facts with regex triggers (or `pattern="*"` for always-on core facts).
  - **Turn-Start Ephemeral Injection:** When a user prompt matches a trigger (including NFC-normalized & accent-insensitive Vietnamese/multilingual prompts like `"kiểm thử"` ↔ `"kiem thu"`), the fact is injected ephemerally for *that turn only* (`PreInvocation` in Antigravity / `UserPromptSubmit` in Claude Code) without polluting future turns or breaking prompt cache prefixes.
  - **Mid-Turn PreToolUse Safety Net:** If a user prompt says *"Fix the bug"* (without mentioning a tool name) and the agent attempts to run a command missing a required flag prescribed by a fact (e.g. running `bruno` without `--condense`), `PreToolUse` catches it and reminds the agent before execution.
* 🧠 **Auto-Learn from Error Recovery:**
  Monitors `PostToolUse` outcomes. When a CLI command fails (`pytest tests/`) and the subsequent command using the same tool succeeds with adjusted flags or runner prefix (`python3 -m pytest tests/ -q`), `centsible` automatically extracts and stores an `auto-learned` micro-fact.
* 🛡️ **Normalized Loop Guard & Circuit Breaker:**
  Monitors tool invocations with CLI flag & metadata normalization (stripping cosmetic retries like `-v`, `-q`, `--tb=short`, or `toolSummary` changes) and deep error extraction (`exitCode`, `is_error`, shell exit markers). If an identical command is issued 3 times or 2 consecutive failures occur, it halts execution:
  - In **Antigravity**: prompts confirmation via decision `"ask"`.
  - In **Claude Code**: blocks the action immediately with exit code `2`.
* 📊 **Real-Time Token ROI & Session Transcript Analytics:**
  Tracks exact ephemeral token overhead vs. discovery and loop-intervention tokens saved, converting net savings into cents (`¢`) / USD (`$`), ROI multiplier, First-Try Command Success Rate (%), and offline JSONL session transcript diagnostics (`centsible stats --transcript`).
* 🤝 **Shared Knowledge & Zero External Dependencies:**
  100% Python standard library with instant startup (`<15ms`), shared seamlessly between Antigravity and Claude Code.

---

## 📦 Installation

### 1. For Google Antigravity (AGY)

#### Global Installation (All Workspaces)
```bash
git clone https://github.com/ntrieuvi97/centsible.git ~/.gemini/config/plugins/centsible
```

#### Workspace-Only Installation
```bash
mkdir -p .agents/plugins
git clone https://github.com/ntrieuvi97/centsible.git .agents/plugins/centsible
```

Antigravity automatically discovers and activates the plugin on startup.

---

### 2. For Claude Code

#### Option A: Native Plugin Install (Recommended — Zero Manual Config)
Inside Claude Code, add the marketplace and install the plugin:
```text
/plugin marketplace add ntrieuvi97/centsible
/plugin install centsible@centsible
```
*(Or via CLI: `claude plugin marketplace add ntrieuvi97/centsible && claude plugin install centsible@centsible`)*

#### Option B: Manual `settings.json` Hook Setup
1. Add `centsible` to your shell PATH:
   ```bash
   ln -sf /path/to/centsible/bin/centsible ~/.local/bin/centsible
   ```
2. Copy the contents of [`claude-hooks.json`](claude-hooks.json) into your `~/.claude/settings.json` (or `.claude/settings.json` in your project).

---

## 🛠️ CLI Usage (`centsible`)

### 1. View System Status & ROI Stats
```bash
centsible status
centsible stats [--rate 3.0] [--transcript path/to/transcript.jsonl] [--json]
centsible --version
```
Output (`centsible stats --transcript transcript.jsonl`):
```text
=== Centsible Efficiency & ROI Report 🪙⚡ ===
• Storage Path:         /Users/.../.agents/facts.jsonl
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
--- Session Transcript Analysis ---
• Transcript:           transcript.jsonl
• Steps / User Turns:   72 steps / 2 turns
• Tool / Cmd Calls:     44 tools (10 commands, 0 failed)
• 1st-Try Cmd Success:  100.0% (Discovery steps before 1st cmd: 0)
• Retry Streaks:        0 streaks (0 wasted retry calls)
```

### 2. Add a Micro-Fact
```bash
centsible facts add --pattern "<regex_or_keyword>" --fact "<one_line_fact>"
```
Examples:
```bash
# Example 1: Bruno test command
centsible facts add --pattern "bruno|run-bruno" --fact "Append --condense to Bruno commands on UAT to save 80% JSON tokens."

# Example 2: Test runner
centsible facts add --pattern "pytest|unit-test" --fact "Run unit tests with 'pytest tests/ -q --tb=short' instead of full suite."
```

### 3. List Stored Facts
```bash
centsible facts list
```

### 4. Test Prompt Matching
Check what facts trigger for a specific user prompt without executing:
```bash
centsible facts test "Can you run bruno tests for me?"
```

### 5. Remove Stale Fact
```bash
centsible facts remove <id_or_pattern>
```

---

## 🏗️ Architecture

```text
User Prompt: "Run bruno test" (or "Chay kiem thu bruno")
      │
      ▼
[ PreInvocation / UserPromptSubmit Hook ]
      │
      ├──> centsible hook (NFC + accent-insensitive regex matcher)
      │      └─ Matches "bruno" pattern & records injected fact IDs
      │      └─ Returns ephemeral injection for this turn only
      ▼
[ Model Execution Turn ]
      │
      ├──> Tool Call Attempt (e.g. run_command: bruno run uat)
      │      │
      │      ▼
      │   [ PreToolUse Hook: Loop Guard + Missing-Flag Fact Check ]
      │      ├─ Normalizes command (strips -v/-q/toolSummary variance)
      │      ├─ Checks identical call count (>=3) & consecutive errors (>=2)
      │      ├─ Checks uninjected facts for missing required flags (--condense)
      │      └─ Decision: "allow" or "ask"/exit(2) with corrective hint
      │      │
      │      ▼
      │   [ PostToolUse Hook: Telemetry & Auto-Learn Recovery ]
      │      ├─ Extracts exit codes / errors & updates 1st-try success metrics
      │      └─ If failed_cmd -> succeeded_cmd on same binary: auto-learns fact!
      ▼
[ Next Turn: "Fix utils.py" ]
      │
      ▼
Prompt has NO bruno fact! Context & Prompt Cache remain 100% clean!
```

---

## 🧪 Testing

Run the included offline test suite:
```bash
python3 -m unittest discover tests
```

---

## 🙏 Acknowledgments

* Inspired by the cost control philosophy and research behind [Microsoft Research's TokenEconomizer](https://github.com/microsoft/TokenEconomizer).
* Built natively for the Google Antigravity and Claude Code developer communities.

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
