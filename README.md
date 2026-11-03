# centsible 🪙⚡

> **Experience-driven cost and token optimization for AI coding agents (Antigravity & Claude Code)**  
> *Make your AI agents work smart and save every token. Centsible = Sensible (smart reasoning) + Cent (cost efficiency).*

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

`centsible` operates as a lightweight, zero-dependency controller sharing a single source of truth (`.agents/facts.jsonl`) across both **Google Antigravity** and **Claude Code**:

* 🧩 **Zero-Bloat Micro-Skills:**
  Learns concise, 1-line repository facts with regex triggers. When a user prompt matches a trigger, the fact is injected ephemerally for *that turn only*, and vanishes afterward:
  - In **Antigravity**: via `PreInvocation` (`ephemeralMessage`).
  - In **Claude Code**: via `UserPromptSubmit` (`hookSpecificOutput.additionalContext`).
* 🛡️ **Loop Guard & Circuit Breaker:**
  Monitors tool invocations. If an identical command is issued 3 times or 2 consecutive failures occur, it halts execution:
  - In **Antigravity**: prompts confirmation via decision `"ask"`.
  - In **Claude Code**: blocks the action immediately with exit code `2`.
* 🤝 **Shared Knowledge:** Discoveries captured via `centsible facts add` in an Antigravity session are immediately available when switching to Claude Code (and vice-versa).
* ⚡ **Zero External Dependencies:** Written 100% in Python standard library. Instant startup (<15ms).

---

## 📦 Installation

### 1. For Google Antigravity (AGY)

#### Global Installation (All Workspaces)
```bash
git clone https://github.com/<your-username>/centsible.git ~/.gemini/config/plugins/centsible
```

#### Workspace-Only Installation
```bash
mkdir -p .agents/plugins
git clone https://github.com/<your-username>/centsible.git .agents/plugins/centsible
```

Antigravity automatically discovers and activates the plugin on startup.

---

### 2. For Claude Code

1. Add `centsible` to your shell PATH:
   ```bash
   ln -sf /path/to/centsible/bin/centsible ~/.local/bin/centsible
   ```
2. Enable Claude Code hooks by adding the following to your `~/.claude/settings.json` (or `.claude/settings.json` in your project):
   ```json
   {
     "hooks": {
       "UserPromptSubmit": [
         {
           "hooks": [
             {
               "type": "command",
               "command": "centsible hook claude-prompt"
             }
           ]
         }
       ],
       "PreToolUse": [
         {
           "matcher": "*",
           "hooks": [
             {
               "type": "command",
               "command": "centsible hook claude-pre-tool"
             }
           ]
         }
       ],
       "PostToolUse": [
         {
           "matcher": "*",
           "hooks": [
             {
               "type": "command",
               "command": "centsible hook claude-post-tool"
             }
           ]
         }
       ]
     }
   }
   ```
   *(A ready-to-use template is available in `claude-hooks.json`).*

---

## 🛠️ CLI Usage (`centsible`)

### 1. View System Status
```bash
centsible status
```
Output:
```text
=== Centsible Status 🪙 ===
• Facts Storage:  /Users/.../.agents/facts.jsonl
• Total Facts:    3
• Total Hits:     14
• Loop Guard:     Enabled (pre-tool threshold: 3 identical, 2 errors)
• Status:         Active
```

### 2. Add a Micro-Fact
```bash
centsible facts add --pattern "<regex_or_keyword>" --fact "<one_line_fact>"
```
Examples:
```bash
# Example 1: Bruno test command
centsible facts add --pattern "bruno|run-bruno" --fact "Append --compact to Bruno commands on staging to save 80% JSON tokens."

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
User Prompt: "Run bruno test"
      │
      ▼
[ PreInvocation / UserPromptSubmit Hook ]
      │
      ├──> centsible hook (stdin: lastUserInput / prompt)
      │      └─ Matcher: matches "bruno" pattern
      │      └─ Returns ephemeral injection for this turn
      ▼
[ Model Execution Turn ]  <-- Sees micro-fact for this turn only!
      │
      ├──> Tool Execution (run_command: bruno --compact)
      │      │
      │      ▼
      │   [ PreToolUse Hook: Loop Guard Check ]
      │      └─ Checks identical call count & consecutive errors
      │      └─ Decision: "allow" (or alerts/blocks if runaway loop detected)
      ▼
[ Next Turn: "Fix utils.py" ]
      │
      ▼
Prompt has NO bruno fact! Context remains 100% clean and minimal!
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
