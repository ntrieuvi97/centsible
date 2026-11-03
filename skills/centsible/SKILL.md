---
name: centsible
description: >-
  Manage repository-specific micro-facts, inspect token optimization status,
  and record reusable discoveries to minimize context bloat and token consumption.
---

# Centsible Skill 🪙

This skill allows the agent and user to manage experience-driven micro-facts and token optimization.

## When to Use
- User asks about token optimization status or active facts (`centsible status`, `centsible facts list`).
- You resolved a non-trivial repo quirk (e.g. non-standard test runner, mandatory environment flag, path alias) that future turns will need.
- Testing pattern matching against prompts.

## Commands

### 1. Check Status & Token ROI Stats
```bash
./bin/centsible status
./bin/centsible stats
./bin/centsible stats --transcript <path_to_transcript.jsonl> --json
```

### Loop Guard Thresholds
View or change how many repeated calls / consecutive failures trigger the loop guard (defaults: 3 identical calls, 2 errors):
```bash
./bin/centsible config list
./bin/centsible config set max_consecutive_errors 4
./bin/centsible config unset max_consecutive_errors
```
Keys: `max_consecutive_errors`, `max_identical_calls`. Env vars `CENTSIBLE_MAX_*` override the file.

### 2. List Micro-Facts
```bash
./bin/centsible facts list
```

### 3. Add a Micro-Fact
Always keep the fact to a single, direct sentence without conversational padding:
```bash
./bin/centsible facts add --pattern "<regex_pattern>" --fact "<one_line_fact>"
```
Examples:
```bash
./bin/centsible facts add --pattern "pytest|unit-test" --fact "Run unit tests with 'pytest tests/ -q --tb=short'."
./bin/centsible facts add --pattern "bruno|api-test" --fact "Append --condense to Bruno commands to save 80% JSON tokens."
```

### 4. Test Fact Matching
Verify what facts trigger for a specific user prompt:
```bash
./bin/centsible facts test "How do I run the bruno tests?"
```

### 5. Remove Stale Fact
```bash
./bin/centsible facts remove <id_or_pattern>
```
