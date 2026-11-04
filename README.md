# centsible 🪙⚡

> **Experience-driven cost and token optimization for AI coding agents (Google Antigravity, Claude Code & OpenClaw)**  
> *Make your AI agents work smart and save every token. Centsible = Sensible (smart reasoning) + Cent (cost efficiency).*

[![Version: 0.2.9](https://img.shields.io/badge/Version-0.2.9-orange.svg)](#)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.8+](https://img.shields.io/badge/Python-3.8+-green.svg)](https://www.python.org/)
[![Zero External Dependencies](https://img.shields.io/badge/Dependencies-Zero%20(Stdlib)-brightgreen.svg)](#)
[![Multi-Agent Runtime: AGY + Claude Code + OpenClaw](https://img.shields.io/badge/Runtimes-Antigravity%20%7C%20Claude%20Code%20%7C%20OpenClaw-purple.svg)](#)

---

## 🎯 The Problem

AI coding agents often burn thousands of unnecessary tokens due to 3 common patterns:
1. **Repetitive Discovery:** In every new conversation, the agent rediscovers basic repository facts (how tests are run, special command flags, path quirks) by grepping and guessing.
2. **Context Bloat:** Developers dump all hints into `AGENTS.md` / `CLAUDE.md`, which are paid for in **every single turn**, causing attention dilution (*lost in the middle*).
3. **Runaway Failure Loops:** Agents retry failing commands or repeated file edits 3–5 times before giving up, wasting context budget.

---

## 💡 How Centsible Solves It

`centsible` operates as a lightweight, zero-dependency controller sharing a single source of truth (`.agents/facts.jsonl` & `.agents/metrics.json`) across **Google Antigravity**, **Claude Code**, and **OpenClaw**:

* 🧩 **Zero-Bloat Micro-Skills & Gotchas (Prompt + Pre-Tool Catch):**
  Learns concise, 1-line repository facts with regex triggers (or `pattern="*"` for always-on core facts).
  - **Turn-Start Ephemeral Injection:** When a user prompt matches a trigger (including NFC-normalized & accent-insensitive Vietnamese/multilingual prompts like `"kiểm thử"` ↔ `"kiem thu"`), the fact is injected ephemerally for *that turn only* (`PreInvocation` in Antigravity / `UserPromptSubmit` in Claude Code) without polluting future turns or breaking prompt cache prefixes.
  - **Mid-Turn PreToolUse Safety Net (CLI & File Paths):** If a user prompt says *"Fix the bug"* (without mentioning a tool name) and the agent attempts to run a command missing a required flag prescribed by a fact (e.g. running `bruno` without `--compact`), or touches a file governed by an architectural rule (`category: gotcha` or `category: rule`), `PreToolUse` catches it and reminds the agent before execution.
  - **User-Correction Auto-Learn & Capture Nudge:** When a user corrects the agent (*"không phải...", "đừng sửa file X", "never touch Y", "always use Z"*), Centsible extracts technical identifiers (filenames, modules, CLI binaries) and automatically persists or nudges the agent to save the gotcha.
* 🧠 **Auto-Learn from Error Recovery & Session Troubleshooting:**
  - **CLI & Script Recovery:** Monitors `PostToolUse` (success) and `PostToolUseFailure` (Claude Code). When a command fails (`pytest tests/`) and the subsequent command using the same tool or interpreter script (`node scripts/migrate.js`, `python3 test.py`) succeeds with adjusted flags or runner prefix (`python3 -m pytest tests/ -q`), `centsible` automatically extracts and stores an `auto-learned` micro-fact. The tool is detected through wrappers (`cd app && rtk proxy pytest`), and failures are remembered for up to 10 tool calls.
  - **Claude Code Stop Hook Nudge:** If an agent encounters repeated tool failures (≥2) in a session, the `Stop` hook intercepts termination once to prompt the agent to persist any discovered gotchas (`centsible facts add --pattern "<target>" --fact "<rule>" --category gotcha`), ensuring hard-won discoveries are not lost when the session ends.
* 🛡️ **Normalized Loop Guard & Circuit Breaker (Multi-Agent):**
  Monitors tool invocations across Antigravity, Claude Code, and OpenClaw (`exec`, `bash`, `run_command`, `edit_file`, `write_file`, `apply_patch`), normalizing cosmetic variations:
  - Strips cosmetic CLI flags (`-v`, `-q`, `--tb=short`, `--no-header`).
  - Strips volatile tool parameters (`WaitMsBeforeAsync`, `timeout`, `toolSummary`, `yieldMs`, `workdir`, `pty`, `background`, `IsDaemon`).
  - Halts runaway loops if an identical command is issued repeatedly or consecutive errors occur:
    - In **Antigravity**: prompts confirmation via decision `"ask"`.
    - In **Claude Code**: blocks the action immediately with exit code `2`.
  - Configurable strictness presets via `centsible config mode` or slash command `/centsible-config`:

    | Mode | Consecutive errors | Identical calls |
    |------|--------------------|-----------------|
    | `ultra` (strict) | 2 | 3 |
    | `normal` (default) | 3 | 4 |
    | `weak` (tolerant) | 4 | 5 |

  - Fine-tune via `centsible config set <key> <n>` or environment variables (`CENTSIBLE_LOOP_MODE`, `CENTSIBLE_MAX_IDENTICAL_CALLS`, `CENTSIBLE_MAX_CONSECUTIVE_ERRORS`).
* 📊 **Real-Time Token ROI & Session Transcript Analytics:**
  Tracks exact ephemeral token overhead vs. discovery and loop-intervention tokens saved, converting net savings into cents (`¢`) / USD (`$`), ROI multiplier, First-Try Command Success Rate (%), and offline JSONL session transcript diagnostics (`centsible stats --transcript`).
* 🤝 **Shared Knowledge & Zero External Dependencies:**
  100% Python standard library with instant startup (`<15ms`), shared seamlessly across all supported agents.

---

## 📦 Installation

### 1. For Google Antigravity (AGY)

#### Global Installation (All Workspaces)
```bash
git clone https://github.com/ntrieuvi97/centsible.git ~/.gemini/config/plugins/centsible
ln -sf ~/.gemini/config/plugins/centsible/bin/centsible ~/.local/bin/centsible
```

#### Workspace-Only Installation
```bash
mkdir -p .agents/plugins
git clone https://github.com/ntrieuvi97/centsible.git .agents/plugins/centsible
```

Antigravity automatically discovers and activates the plugin on startup (and automatically links `~/.local/bin/centsible` on first run if `~/.local/bin` exists).

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

### 2. Configure Loop Guard Strictness
Manage circuit breaker thresholds and presets:
```bash
# View active configuration and effective thresholds
centsible config list

# Switch loop guard mode preset (ultra: 2 err / 3 calls, normal: 3 err / 4 calls, weak: 4 err / 5 calls)
centsible config mode ultra
centsible config mode normal

# Override specific thresholds
centsible config set max_consecutive_errors 4
centsible config set max_identical_calls 5
centsible config unset max_consecutive_errors
```
*(In Claude Code, you can also run the slash command `/centsible-config mode ultra`)*

### 3. Add a Micro-Fact or Architectural Gotcha
```bash
centsible facts add --pattern "<regex_or_keyword>" --fact "<one_line_fact>" [--category gotcha|rule|general]
```
Examples:
```bash
# Example 1: CLI command optimization
centsible facts add --pattern "bruno|run-bruno" --fact "Append --compact to Bruno commands on staging to save 80% JSON tokens."

# Example 2: Test runner flags
centsible facts add --pattern "pytest|unit-test" --fact "Run unit tests with 'pytest tests/ -q --tb=short' instead of full suite."

# Example 3: Architectural / File gotcha (triggers in PreToolUse when editing matching paths)
centsible facts add --pattern "auth_service.py|auth/" --fact "Never mutate token signing keys directly; use TokenKeyRotator." --category gotcha
```

### 4. List Stored Facts
```bash
centsible facts list
```

### 5. Test Prompt Matching
Check what facts trigger for a specific user prompt without executing:
```bash
centsible facts test "Can you run bruno tests for me?"
```

### 6. Remove Stale Fact
```bash
centsible facts remove <id_or_pattern>
```

---

## 🏗️ Architecture

```text
User Prompt: "Run bruno test" (or user correction "Đừng sửa auth_service.py trực tiếp")
      │
      ▼
[ PreInvocation / UserPromptSubmit Hook ]
      │
      ├──> centsible hook (NFC + accent-insensitive regex matcher)
      │      ├─ Matches prompt pattern & returns ephemeral injection for this turn only
      │      └─ Detects user corrections & auto-saves gotcha rule / capture nudge
      ▼
[ Model Execution Turn ]
      │
      ├──> Tool Call Attempt (run_command, exec, edit_file, write_file, ...)
      │      │
      │      ▼
      │   [ PreToolUse Hook: Loop Guard + Missing Flags & File Gotchas ]
      │      ├─ Normalizes command & strips volatile args (WaitMsBeforeAsync, timeout, workdir)
      │      ├─ Checks identical calls (>=3..5) & consecutive errors (>=2..4)
      │      ├─ Checks CLI commands for missing required flags (--compact)
      │      ├─ Checks file editing/reading paths against architectural gotchas & rules
      │      └─ Decision: "allow" or "ask"/exit(2) with corrective hint
      │      │
      │      ▼
      │   [ PostToolUse / PostToolUseFailure Hook: Telemetry & Auto-Learn ]
      │      ├─ Extracts exit codes / errors & updates 1st-try success metrics
      │      └─ If failed -> succeeded on same binary/script: auto-learns micro-fact!
      ▼
[ Turn Completion / Session Exit ]
      │
      ▼
   [ Stop Hook (Claude Code) ]
      └─ If session experienced repeated failures (>=2), nudges agent to save gotchas
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
* Built natively for the Google Antigravity, Claude Code, and OpenClaw developer communities.

---

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
