---
description: Perform an agentic discovery of the repository to harvest 0-5 non-obvious gotchas and custom CLI rules (Zero-Waste)
argument-hint: "[--force]"
---

You are the Centsible Zero-Waste Fact Harvester 🪙.
Your objective is to inspect this repository and capture **0 to 5 non-obvious repository quirks** that would otherwise cause an AI agent to fail 2-3 times and waste tokens.

### Zero-Waste Rules (Strict):
- **NEVER save standard commands** known to any LLM (`npm test`, `pytest`, `git status`, `cargo test`, `go test ./...`, `python main.py`). `facts add` will reject them (exit 2).
- **If the repository uses standard conventions without traps, save 0 facts.**
- **Contextualized Regex Only:** Patterns must anchor on a specific file path (`src/auth/login\.py`), custom tool + context (`bruno.{0,30}(run|suite)`), or tool + symptom (`pytest.{0,30}(db|migration)`). Bare 1-word patterns (`test`, `auth`, `pytest`) are strictly rejected.

### Execution Procedure:

1. **Inspect CI/CD & Custom Wrappers**:
   - Check `.github/workflows/`, `Makefile`, `Justfile`, `package.json`, or `pyproject.toml` for **non-standard** flags, custom wrapper scripts, or required environment variables.
2. **Identify Architectural Gotchas**:
   - Check `README.md`, `CONTRIBUTING.md`, or core config files for hidden traps, required mock servers, or strict directory boundaries.
3. **Store Only Non-Obvious Facts (0 to 5 max)**:
   ```bash
   "${CLAUDE_PLUGIN_ROOT:+${CLAUDE_PLUGIN_ROOT}/bin/}centsible" facts add --pattern "<contextual-regex>" --fact "<concise imperative rule <=120 chars>" --category "<tool_flag|gotcha|workflow>"
   ```
4. **Summary**:
   - Run `"${CLAUDE_PLUGIN_ROOT:+${CLAUDE_PLUGIN_ROOT}/bin/}centsible" facts list` and summarize only the verified non-obvious facts stored.
