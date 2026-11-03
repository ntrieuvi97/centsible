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


sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.loop_guard import get_state_path  # noqa: E402

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

    def test_claude_failure_then_success_auto_learns_fact(self):
        sid = f"claude_learn_{id(self)}"

        def payload(event, cmd, **extra):
            return json.dumps({
                "hook_event_name": event, "tool_name": "Bash", "tool_input": {"command": cmd},
                "session_id": sid, "cwd": self.temp_dir.name, **extra,
            })

        # Claude Code sends failures via PostToolUseFailure with a plain 'error' string
        res = self._run_te(["hook", "claude-post-tool-failure"], payload("PostToolUseFailure", "cd app && pytest tests/", error="Exit code 1"))
        self.assertEqual(res.returncode, 0)
        res = self._run_te(["hook", "claude-post-tool"], payload("PostToolUse", "cd app && python3 -m pytest tests/ -q"))
        self.assertEqual(res.returncode, 0)

        facts = [json.loads(l) for l in self.facts_path.read_text().splitlines() if l.strip()]
        self.assertEqual([f["category"] for f in facts], ["auto-learned"])
        get_state_path(sid).unlink(missing_ok=True)

    def test_claude_user_correction_and_smooth_pre_tool_gotcha_injection(self):
        sid = f"claude_gotcha_{id(self)}"
        # 1. User submits a correction prompt in session 1
        prompt_payload = json.dumps({
            "prompt": "Lưu ý: đừng sửa legacy_auth.py, phải sửa ở jwt_middleware.py",
            "cwd": self.temp_dir.name,
            "session_id": sid,
        })
        res = self._run_te(["hook", "claude-prompt"], prompt_payload)
        self.assertEqual(res.returncode, 0)
        out = json.loads(res.stdout)
        self.assertIn("Auto-Learned Gotcha", out["hookSpecificOutput"]["additionalContext"])

        # 2. In a new session, user says 'fix login bug' and Claude edits legacy_auth.py
        sid2 = f"claude_gotcha_s2_{id(self)}"
        pre_payload = json.dumps({
            "tool_name": "Edit",
            "tool_input": {"file_path": "/app/src/legacy_auth.py"},
            "cwd": self.temp_dir.name,
            "session_id": sid2,
        })
        res2 = self._run_te(["hook", "claude-pre-tool"], pre_payload)
        # Must exit 0 (not block Edit) while injecting additionalContext
        self.assertEqual(res2.returncode, 0)
        out2 = json.loads(res2.stdout)
        self.assertEqual(out2["hookSpecificOutput"]["permissionDecision"], "allow")
        self.assertIn("jwt_middleware.py", out2["hookSpecificOutput"]["additionalContext"])
        get_state_path(sid).unlink(missing_ok=True)
        get_state_path(sid2).unlink(missing_ok=True)

    def test_claude_native_plugin_manifests_and_plugin_root_hook(self):
        plugin_manifest = json.loads((REPO_ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        marketplace_manifest = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
        hooks_cfg = json.loads((REPO_ROOT / "claude-hooks.json").read_text(encoding="utf-8"))

        self.assertEqual(plugin_manifest["name"], "centsible")
        self.assertIn("./claude-hooks.json", plugin_manifest["hooks"])
        self.assertIn("PostToolUseFailure", hooks_cfg["hooks"])
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
