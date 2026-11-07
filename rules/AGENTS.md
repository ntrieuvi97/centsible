# Centsible Rules

1. **Fail-Fast Circuit Breaker**: Stop after 2 consecutive identical command/edit failures. Inspect files or diagnose root cause before retrying.
2. **Apply Micro-Skills**: Strictly follow injected `[CENTSIBLE MICRO-SKILLS]` and `[Centsible Micro-Skill Hint]` rules.
3. **Zero-Waste Self-Learning**:
   - NEVER save standard commands known to LLMs (`npm test`, `git status`, `python main.py`, `pytest`, `cargo build`).
   - Only record non-obvious repo quirks discovered after >= 2 failures (unique flags, hidden env vars, critical path rules).
   - Patterns MUST be contextualized regexes (`tool.{0,30}(flag|symptom)` or `src/auth/`), never single words (`test`, `auth`).
   - Do not call `facts add` on routine sessions.
