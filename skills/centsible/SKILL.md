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

### 1. Check Status
```bash
./bin/centsible status
```

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
./bin/centsible facts add --pattern "bruno|api-test" --fact "Append --compact to Bruno commands to save 80% JSON tokens."
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
