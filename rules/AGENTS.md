# Centsible — Rules 🪙

These operational guidelines ensure maximum token efficiency and prevent runaway loops:

## 1. Circuit Breaker & Fail-Fast (Chống lặp thất bại)
- If a terminal command or file modification fails 2 consecutive times with the same error signature, **STOP IMMEDIATELY**.
- Do not blindly retry the same command a third time.
- Diagnose the root cause, inspect files, or ask the user for clarification before executing further mutations.

## 2. Respect Injected Micro-Facts (Tận dụng Micro-Skills)
- When `[CENTSIBLE MICRO-SKILLS]` are injected in your turn context, strictly follow them.
- They represent verified repository-specific facts (e.g., specific test flags, required env vars, file paths) learned from past sessions to save research tokens.

## 3. Autonomous In-Turn Self-Learning (Tự học ngay trong phiên — Zero Extra Turn)
- **Zero-Extra-Turn Policy:** Never wait for an end-of-session Stop hook or user reminder to record facts.
- **Save Before Final Response:** Before writing your final textual answer to the user on any task that touched files or ran commands, perform a quick self-reflection:
  - Did you verify or resolve any repo-specific test command, build flag, environment quirk, or architecture convention?
  - If **YES**: Execute `./bin/centsible facts add --pattern "<pattern>" --fact "<fact>" --category "<category>"` as your LAST tool call BEFORE emitting your final textual response.
  - If **NO**: Proceed directly to your final response without recording bloat.
- Doing this in-turn guarantees your rich final answer remains prominently visible to the user without being collapsed or superseded by an unnecessary continuation turn.
- Keep facts strictly to **one concise imperative line** (≤ 120 chars) without conversational filler.
