---
description: Update every centsible install on this machine (Claude Code plugin, git clones, pip) to the latest release
argument-hint: "[--check]"
---

Run `"${CLAUDE_PLUGIN_ROOT:+${CLAUDE_PLUGIN_ROOT}/bin/}centsible" update $ARGUMENTS` and show the report. If it updated the Claude Code plugin, tell the user to restart Claude Code to load the new version.
