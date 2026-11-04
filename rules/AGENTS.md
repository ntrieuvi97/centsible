# Centsible — Rules 🪙

These operational guidelines ensure maximum token efficiency and prevent runaway loops:

## 1. Circuit Breaker & Fail-Fast (Chống lặp thất bại)
- If a terminal command or file modification fails 2 consecutive times with the same error signature, **STOP IMMEDIATELY**.
- Do not blindly retry the same command a third time.
- Diagnose the root cause, inspect files, or ask the user for clarification before executing further mutations.

## 2. Respect Injected Micro-Facts (Tận dụng Micro-Skills)
- When `[CENTSIBLE MICRO-SKILLS]` are injected in your turn context, strictly follow them.
- They represent verified repository-specific facts (e.g., specific test flags, required env vars, file paths) learned from past sessions to save research tokens.

## 3. Autonomous Self-Learning & Gotcha Capture (Tự học & Đúc kết kinh nghiệm)
- **Immediate Capture:** When resolving a tricky gotcha, obscure command flag, or environment quirk that would otherwise take multiple turns to rediscover, record it immediately:
  ```bash
  ./bin/centsible facts add --pattern "<regex_or_keyword>" --fact "<one_line_summary>" --category gotcha
  ```
- **End-of-Session Wrap-Up Check:** Before concluding any task or session where you executed commands or edited files, perform a quick self-reflection:
  - Did you discover or verify any repo-specific test command, build tool, required flag, or non-obvious architecture pattern?
  - If YES: Save it with `./bin/centsible facts add --pattern "<pattern>" --fact "<fact>" --category "<category>"` before your final response.
  - If NO new patterns were discovered: Complete normally without adding bloat.
- Keep facts strictly to **one concise imperative line** (≤ 120 chars) without conversational filler.
