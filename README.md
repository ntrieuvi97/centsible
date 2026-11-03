# agy-token-economizer ⚡

> **Experience-driven cost and token optimization plugin for Google Antigravity (AGY)**  
> *Inspired by Microsoft Research's TokenEconomizer philosophy, tailored natively for the Antigravity agent runtime.*

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python: 3.8+](https://img.shields.io/badge/Python-3.8+-green.svg)](https://www.python.org/)
[![Zero External Dependencies](https://img.shields.io/badge/Dependencies-Zero%20(Stdlib)-brightgreen.svg)](#)

---

## 🎯 The Problem

AI coding agents often burn thousands of unnecessary tokens due to 3 common patterns:
1. **Repetitive Discovery:** In every new conversation, the agent rediscovers basic repository facts (how tests are run, special command flags, path quirks) by grepping and guessing.
2. **Context Bloat:** Developers dump all hints into `AGENTS.md` or system prompts, which are paid for in **every single turn**, causing attention dilution (*lost in the middle*).
3. **Runaway Failure Loops:** Agents retry failing commands or repeated file edits 3–5 times before giving up, wasting context budget.

---

## 💡 How `agy-token-economizer` Solves It

`agy-token-economizer` operates as a lightweight, zero-dependency controller natively integrated into Antigravity:

* 🧩 **Zero-Bloat Micro-Skills (`PreInvocation` Hook):**
  Learns concise, 1-line repository facts with regex triggers. When a user prompt matches a trigger, the fact is injected as an **`ephemeralMessage`** for *that turn only*, and vanishes afterward. It **never** bloats `AGENTS.md` or future turns.
* 🛡️ **Loop Guard & Circuit Breaker (`PreToolUse` Hook):**
  Monitors tool invocations. If an identical command is issued 3 times or 2 consecutive failures occur, it halts execution and prompts the user/agent to redirect strategy immediately.
* ⚡ **Zero External Dependencies:**
  Written 100% in Python standard library. Instant startup (<15ms) on macOS, Linux, and Windows.

---

## 📦 Installation

### Option 1: Global Plugin (All Workspaces)
Clone into your global Antigravity plugins directory:
```bash
git clone https://github.com/ahamove/agy-token-economizer.git ~/.gemini/config/plugins/agy-token-economizer
```

### Option 2: Workspace-Only Plugin
Clone into a specific repository's `.agents/plugins` directory:
```bash
mkdir -p .agents/plugins
git clone https://github.com/ahamove/agy-token-economizer.git .agents/plugins/agy-token-economizer
```

Antigravity automatically discovers and activates the plugin on startup.

---

## 🛠️ CLI Usage (`te`)

Add `./bin/te` to your `PATH` or invoke it directly:

### 1. View System Status
```bash
./bin/te status
```
Output:
```text
=== AGY TokenEconomizer Status ===
• Facts Storage:  /Users/.../.agents/facts.jsonl
• Total Facts:    3
• Total Hits:     14
• Loop Guard:     Enabled (pre-tool threshold: 3 identical, 2 errors)
• Status:         Active
```

### 2. Add a Micro-Fact
```bash
./bin/te facts add --pattern "<regex_or_keyword>" --fact "<one_line_fact>"
```
Examples:
```bash
# Example 1: Bruno test command
./bin/te facts add --pattern "bruno|run-bruno" --fact "Append --condense to Bruno commands on UAT to save 80% JSON tokens."

# Example 2: Test runner
./bin/te facts add --pattern "pytest|unit-test" --fact "Run unit tests with 'pytest tests/ -q --tb=short' instead of full suite."
```

### 3. List Stored Facts
```bash
./bin/te facts list
```

### 4. Test Prompt Matching
Check what facts trigger for a specific user prompt without executing:
```bash
./bin/te facts test "Can you run bruno tests for me?"
```

### 5. Remove Stale Fact
```bash
./bin/te facts remove <id_or_pattern>
```

---

## 🏗️ Architecture

```text
User Prompt: "Run bruno test"
      │
      ▼
[ Antigravity PreInvocation Hook ]
      │
      ├──> te hook pre-invocation (stdin: lastUserInput)
      │      └─ Matcher: matches "bruno" pattern
      │      └─ stdout: {"injectSteps": [{"ephemeralMessage": "[MICRO-FACTS] ..."}]}
      ▼
[ Model Execution Turn ]  <-- Sees micro-fact for this turn only!
      │
      ├──> Tool Execution (run_command: bruno --condense)
      │      │
      │      ▼
      │   [ PreToolUse Hook: Loop Guard Check ]
      │      └─ Checks identical call count & consecutive errors
      │      └─ Decision: "allow" (or "ask" if runaway loop detected)
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

## 📄 License

MIT License. See [LICENSE](LICENSE) for details.
