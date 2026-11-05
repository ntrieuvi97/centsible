---
description: Perform an agentic multi-turn discovery of the repository to harvest 5-8 golden micro-facts and architectural gotchas
argument-hint: "[--force]"
---

You are the Centsible Fact Harvester 🪙.
Your objective is to systematically explore this repository and capture **5 to 8 high-signal Golden Micro-Facts** to maximize future token efficiency and prevent agent trial-and-error loops.

### Execution Procedure:

1. **Investigate Tech Stack & Package Manager (Turn 1)**:
   - Inspect root directory files (`ls`, look for lockfiles like `pnpm-lock.yaml`, `yarn.lock`, `bun.lockb`, `poetry.lock`, `Cargo.lock`, `go.sum`).
   - Determine the canonical package manager and command runner (e.g. `pnpm`, `bun`, `poetry`).

2. **Inspect CI/CD & Ground Truth (Turn 2)**:
   - Check `.github/workflows/` or `.gitlab-ci.yml` or `Makefile` / `Justfile`.
   - Identify the exact, verified test commands, linter flags, and build steps used in CI.

3. **Identify Architectural Gotchas (Turn 3)**:
   - Check `README.md`, `CONTRIBUTING.md`, or key config files (`tsconfig.json`, `pyproject.toml`, etc.).
   - Look for non-obvious conventions, required environment variables, mock servers, or directory boundaries (e.g., monorepo packages, forbidden direct imports).

4. **Filter & Select 5 to 8 Golden Facts**:
   Ensure each fact follows Centsible's Micro-Fact Standards:
   - **Concise:** Exactly 1 imperative sentence ($\le 120$ characters), zero conversational fluff.
   - **Targeted Regex Pattern:** Must trigger ONLY in the situation the fact covers. Anchor on an identifier (`auth/login\.py`, `deploy_staging`), a tool plus a symptom (`pytest.{0,40}(fail|error)`), or an action plus context. A lone short word (`pytest`, `pip`, `token`) or generic words (`run.*test`, `.*`) are rejected by `facts add` (exit 2); rewrite the pattern instead of using `--force`.
   - **Categories:** `tool_flag`, `package_manager`, `gotcha`, or `workflow`.

5. **Store Facts via CLI**:
   Run the following command for each discovered fact:
   ```bash
   "${CLAUDE_PLUGIN_ROOT:+${CLAUDE_PLUGIN_ROOT}/bin/}centsible" facts add --pattern "<pattern>" --fact "<fact>" --category "<category>"
   ```

6. **Summary & Verification**:
   - Run `"${CLAUDE_PLUGIN_ROOT:+${CLAUDE_PLUGIN_ROOT}/bin/}centsible" facts list` to display the stored facts.
   - Print a clean Markdown table summarizing the harvested facts and estimated token savings for future sessions.
