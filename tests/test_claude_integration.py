#!/usr/bin/env python3
"""
Integration tests for Claude Code hooks.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


class TestClaudeIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.facts_path = Path(self.temp_dir.name) / ".agents" / "facts.jsonl"
        self.facts_path.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run_te(self, args, stdin_data=None):
        cmd = [sys.executable, str(REPO_ROOT / "core" / "cli.py")] + args
        return subprocess.run(
            cmd,
            input=stdin_data,
            capture_output=True,
            text=True,
            cwd=self.temp_dir.name,
        )

    def test_claude_prompt_hook_injects_additional_context(self):
        # 1. Add fact
        self._run_te([
            "facts", "add",
            "--pattern", "docker",
            "--fact", "Use docker compose v2 commands",
            "--id", "fact-docker",
        ])

        # 2. Simulate Claude Code UserPromptSubmit payload
        claude_payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "claude-session-123",
            "cwd": self.temp_dir.name,
            "prompt": "Please start the docker containers for me",
        }

        res = self._run_te(
            ["hook", "claude-prompt"],
            stdin_data=json.dumps(claude_payload),
        )
        self.assertEqual(res.returncode, 0)
        output = json.loads(res.stdout)
        self.assertIn("hookSpecificOutput", output)
        self.assertIn("additionalContext", output["hookSpecificOutput"])
        self.assertIn("Use docker compose v2 commands", output["hookSpecificOutput"]["additionalContext"])

    def test_claude_prompt_hook_no_match(self):
        claude_payload = {
            "hook_event_name": "UserPromptSubmit",
            "session_id": "claude-session-123",
            "cwd": self.temp_dir.name,
            "prompt": "Just saying hello",
        }

        res = self._run_te(
            ["hook", "claude-prompt"],
            stdin_data=json.dumps(claude_payload),
        )
        self.assertEqual(res.returncode, 0)
        output = json.loads(res.stdout)
        self.assertEqual(output, {})

    def test_claude_pre_tool_loop_guard_exit_code_2(self):
        claude_tool_call = {
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "failing_command"},
            "session_id": f"claude_test_{id(self)}",
        }
        stdin_str = json.dumps(claude_tool_call)

        # Call 1 -> exit 0
        res1 = self._run_te(["hook", "claude-pre-tool"], stdin_data=stdin_str)
        self.assertEqual(res1.returncode, 0)

        # Call 2 -> exit 0
        res2 = self._run_te(["hook", "claude-pre-tool"], stdin_data=stdin_str)
        self.assertEqual(res2.returncode, 0)

        # Call 3 -> exit 2 (Claude Code block code) + stderr
        res3 = self._run_te(["hook", "claude-pre-tool"], stdin_data=stdin_str)
        self.assertEqual(res3.returncode, 2)
        self.assertIn("[LoopGuard Alert]", res3.stderr)

    def test_claude_native_plugin_manifests_and_plugin_root_hook(self):
        plugin_manifest = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        marketplace_manifest = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        hooks_cfg = json.loads((REPO_ROOT / "claude-hooks.json").read_text(encoding="utf-8"))

        self.assertEqual(plugin_manifest["name"], "centsible")
        self.assertIn("./claude-hooks.json", plugin_manifest["hooks"])
        self.assertEqual(marketplace_manifest["plugins"][0]["name"], "centsible")

        # Execute the exact shell command from claude-hooks.json with CLAUDE_PLUGIN_ROOT set
        cmd_str = hooks_cfg["hooks"]["UserPromptSubmit"][0]["hooks"][0]["command"]
        env = dict(subprocess.os.environ, CLAUDE_PLUGIN_ROOT=str(REPO_ROOT))
        res = subprocess.run(
            cmd_str,
            shell=True,
            input=json.dumps({"prompt": "hello", "cwd": self.temp_dir.name}),
            capture_output=True,
            text=True,
            env=env,
        )
        self.assertEqual(res.returncode, 0)
        self.assertEqual(json.loads(res.stdout), {})


if __name__ == "__main__":
    unittest.main()
