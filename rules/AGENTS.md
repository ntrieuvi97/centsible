# AGY TokenEconomizer — Rules

These operational guidelines ensure maximum token efficiency and prevent runaway loops:

## 1. Circuit Breaker & Fail-Fast (Chống lặp thất bại)
- If a terminal command or file modification fails 2 consecutive times with the same error signature, **STOP IMMEDIATELY**.
- Do not blindly retry the same command a third time.
- Diagnose the root cause, inspect files, or ask the user for clarification before executing further mutations.

## 2. Respect Injected Micro-Facts (Tận dụng Micro-Skills)
- When `[TOKEN-ECONOMIZER MICRO-SKILLS]` are injected in your turn context, strictly follow them.
- They represent verified repository-specific facts (e.g., specific test flags, required env vars, file paths) learned from past sessions to save research tokens.

## 3. Capture Reusable Discoveries (Ghi nhận kinh nghiệm mới)
- When resolving a tricky gotcha, obscure command flag, or environment quirk that would otherwise take multiple turns to rediscover, record it using:
  ```bash
  ./bin/te facts add --pattern "<regex_or_keyword>" --fact "<one_line_summary>"
  ```
- Keep facts strictly to **one concise line** without conversational filler.
