# Centsible — Rules 🪙

These operational guidelines ensure maximum token efficiency and prevent runaway loops:

## 1. Circuit Breaker & Fail-Fast (Chống lặp thất bại)
- If a terminal command or file modification fails 2 consecutive times with the same error signature, **STOP IMMEDIATELY**.
- Do not blindly retry the same command a third time.
- Diagnose the root cause, inspect files, or ask the user for clarification before executing further mutations.

## 2. Respect Injected Micro-Facts (Tận dụng Micro-Skills)
- When `[CENTSIBLE MICRO-SKILLS]` are injected in your turn context, strictly follow them.
- They represent verified repository-specific facts (e.g., specific test flags, required env vars, file paths) learned from past sessions to save research tokens.

## 3. Lean Self-Learning (Tự học tinh gọn — Zero Waste)
- **Do Not Bloat Turn Endings:** Never execute speculative `facts add` commands at the end of a session unless an unexpected, non-obvious repository quirk or critical command flag was newly resolved and verified.
- **Save Only High-Signal Facts:** When saving a fact, keep it strictly to **one concise imperative line** (≤ 120 chars) with a precise regex trigger: `./bin/centsible facts add --pattern "<pattern>" --fact "<fact>" --category "<category>"`.
- If no critical repo-specific quirk was resolved, proceed directly to your response without running extra tools.
