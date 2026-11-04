#!/usr/bin/env python3
"""
CLI and Hook integration tests.
"""

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


class TestCliIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.facts_path = Path(self.temp_dir.name) / ".agents" / "facts.jsonl"
        self.facts_path.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run_te(self, args, stdin_data=None, env=None):
        cmd = [sys.executable, str(REPO_ROOT / "core" / "cli.py")] + args
        custom_env = dict(subprocess.os.environ, CENTSIBLE_HOME=self.temp_dir.name)
        for k in ("CENTSIBLE_LOOP_MODE", "CENTSIBLE_MAX_CONSECUTIVE_ERRORS", "CENTSIBLE_MAX_IDENTICAL_CALLS"):
            custom_env.pop(k, None)
        if env:
            custom_env.update(env)
        result = subprocess.run(
            cmd,
            input=stdin_data,
            capture_output=True,
            text=True,
            cwd=self.temp_dir.name,
            env=custom_env,
        )
        return result

    def test_config_cli(self):
        env = {"CENTSIBLE_HOME": self.temp_dir.name, "CENTSIBLE_MAX_CONSECUTIVE_ERRORS": ""}
        res = self._run_te(["config", "set", "max_consecutive_errors", "4"], env=env)
        self.assertEqual(res.returncode, 0)
        self.assertIn("max_consecutive_errors = 4", res.stdout)
        self.assertIn("max_consecutive_errors = 4", self._run_te(["config", "list"], env=env).stdout)
        self.assertNotEqual(self._run_te(["config", "set", "max_identical_calls", "0"], env=env).returncode, 0)
        res = self._run_te(["config", "unset", "max_consecutive_errors"], env=env)
        self.assertIn("max_consecutive_errors = 3", res.stdout)

    def test_config_mode_cli(self):
        env = {"CENTSIBLE_HOME": self.temp_dir.name, "CENTSIBLE_LOOP_MODE": "", "CENTSIBLE_MAX_CONSECUTIVE_ERRORS": ""}
        res = self._run_te(["config", "mode", "weak"], env=env)
        self.assertEqual(res.returncode, 0)
        self.assertIn("mode = weak", res.stdout)
        self.assertIn("max_consecutive_errors = 4", res.stdout)
        self.assertNotEqual(self._run_te(["config", "mode", "bogus"], env=env).returncode, 0)
        self.assertIn("mode = normal", self._run_te(["config", "unset", "mode"], env=env).stdout)

    def test_facts_crud_cli(self):
        # 1. Add fact
        res_add = self._run_te([
            "facts", "add",
            "--pattern", "npm-build",
            "--fact", "Run npm run build --silent",
            "--id", "fact-npm",
        ])
        self.assertEqual(res_add.returncode, 0)
        self.assertIn("✓ Added micro-fact [fact-npm]", res_add.stdout)

        # 2. List facts
        res_list = self._run_te(["facts", "list"])
        self.assertEqual(res_list.returncode, 0)
        self.assertIn("fact-npm", res_list.stdout)
        self.assertIn("npm-build", res_list.stdout)

        # 3. Test fact matching
        res_test = self._run_te(["facts", "test", "Please execute npm-build now"])
        self.assertEqual(res_test.returncode, 0)
        self.assertIn("Matched 1 fact(s)", res_test.stdout)

        # 4. Remove fact
        res_del = self._run_te(["facts", "remove", "fact-npm"])
        self.assertEqual(res_del.returncode, 0)
        self.assertIn("✓ Removed fact", res_del.stdout)

    def test_pre_invocation_hook_contract(self):
        # Seed a fact
        self._run_te([
            "facts", "add",
            "--pattern", "bruno",
            "--fact", "Use --condense flag on UAT",
            "--id", "fact-bruno",
        ])

        # Hook payload sent by Antigravity
        payload = {
            "lastUserInput": "Can you run bruno tests for me?",
            "workspacePaths": [self.temp_dir.name],
        }

        res = self._run_te(
            ["hook", "pre-invocation"],
            stdin_data=json.dumps(payload),
        )
        self.assertEqual(res.returncode, 0)

        data = json.loads(res.stdout)
        self.assertIn("injectSteps", data)
        self.assertEqual(len(data["injectSteps"]), 1)
        self.assertIn("ephemeralMessage", data["injectSteps"][0])
        self.assertIn("Use --condense flag on UAT", data["injectSteps"][0]["ephemeralMessage"])

    def test_pre_invocation_no_match_returns_empty(self):
        payload = {
            "lastUserInput": "Hello there, just checking in",
            "workspacePaths": [self.temp_dir.name],
        }

        res = self._run_te(
            ["hook", "pre-invocation"],
            stdin_data=json.dumps(payload),
        )
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertEqual(data, {})

    def test_status_command(self):
        res = self._run_te(["status"])
        self.assertEqual(res.returncode, 0)
        self.assertIn("=== Centsible Status", res.stdout)
        self.assertIn("Loop Guard:     Enabled", res.stdout)
        self.assertIn("Tokens Saved:", res.stdout)
        self.assertIn("mode normal: 4 identical, 3 errors", res.stdout)

    def test_symlinked_bin_execution(self):
        # Verify bin/centsible works when invoked via a symlink in another directory (e.g. ~/.local/bin/centsible)
        symlink_path = Path(self.temp_dir.name) / "centsible_link"
        symlink_path.symlink_to(REPO_ROOT / "bin" / "centsible")
        res = subprocess.run(
            [str(symlink_path), "stats"],
            capture_output=True,
            text=True,
            cwd=self.temp_dir.name,
        )
        self.assertEqual(res.returncode, 0)
        self.assertIn("=== Centsible Efficiency & ROI Report", res.stdout)

    def test_stats_and_transcript_analysis(self):
        # 1. Seed a fact and trigger a hit
        self._run_te([
            "facts", "add",
            "--pattern", "pytest",
            "--fact", "Use pytest -q",
            "--id", "fact-pytest",
        ])
        self._run_te(
            ["hook", "pre-invocation"],
            stdin_data=json.dumps({
                "lastUserInput": "Run pytest suite",
                "workspacePaths": [self.temp_dir.name],
                "conversationId": "conv_stats_test",
            }),
        )

        # 2. Create a mock transcript.jsonl
        transcript_path = Path(self.temp_dir.name) / "transcript.jsonl"
        steps = [
            {"type": "USER_INPUT", "content": "Run tests"},
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "read_file", "args": {"Path": "README.md"}}]},
            {"type": "PLANNER_RESPONSE", "tool_calls": [{"name": "run_command", "args": {"CommandLine": "pytest -q"}}], "status": "DONE"},
        ]
        transcript_path.write_text("\n".join(json.dumps(s) for s in steps) + "\n", encoding="utf-8")

        # 3. Run stats --json --transcript
        res = self._run_te(["stats", "--json", "--transcript", str(transcript_path)])
        self.assertEqual(res.returncode, 0)
        report = json.loads(res.stdout)
        self.assertEqual(report["metrics"]["fact_hits"], 1)
        self.assertGreater(report["metrics"]["net_saved_tokens"], 1400)
        self.assertEqual(report["transcript"]["user_turns"], 1)
        self.assertEqual(report["transcript"]["discovery_steps_to_first_cmd"], 1)
        self.assertEqual(report["transcript"]["first_try_cmd_rate"], 100.0)

    def test_analyze_transcript_openclaw_exec(self):
        transcript_path = Path(self.temp_dir.name) / "transcript_openclaw.jsonl"
        steps = [
            {"type": "user", "content": "Run build"},
            {"type": "assistant", "tool_calls": [{"name": "read", "args": {"path": "package.json"}}]},
            {"type": "assistant", "tool_calls": [{"name": "exec", "args": {"command": "npm run build"}}], "status": "DONE"},
        ]
        transcript_path.write_text("\n".join(json.dumps(s) for s in steps) + "\n", encoding="utf-8")

        res = self._run_te(["stats", "--json", "--transcript", str(transcript_path)])
        self.assertEqual(res.returncode, 0)
        report = json.loads(res.stdout)
        self.assertEqual(report["transcript"]["tool_calls"], 2)
        self.assertEqual(report["transcript"]["command_calls"], 1)
        self.assertEqual(report["transcript"]["discovery_steps_to_first_cmd"], 1)
        self.assertEqual(report["transcript"]["first_try_cmd_rate"], 100.0)

    def test_status_tip_when_empty(self):
        res = self._run_te(["status"])
        self.assertEqual(res.returncode, 0)
        self.assertIn("Total Facts:    0", res.stdout)
        self.assertIn("Tip:            No facts found", res.stdout)

    def test_harvest_cli_flow(self):
        # 1. Harvest when empty
        res = self._run_te(["harvest"])
        self.assertEqual(res.returncode, 0)
        self.assertIn("To harvest 5-8 golden architectural facts", res.stdout)
        self.assertIn("Type '/centsible-harvest'", res.stdout)

        # 2. Harvest --prompt
        res_prompt = self._run_te(["harvest", "--prompt"])
        self.assertEqual(res_prompt.returncode, 0)
        self.assertIn("You are the Centsible Fact Harvester", res_prompt.stdout)
        self.assertIn("Execution Procedure", res_prompt.stdout)

        # 3. Add a fact, then verify harvest reports already active
        self._run_te([
            "facts", "add",
            "--pattern", "pytest",
            "--fact", "Run pytest -q",
            "--id", "fact-1",
        ])
        res_has_facts = self._run_te(["harvest"])
        self.assertEqual(res_has_facts.returncode, 0)
        self.assertIn("Repo already has active micro-facts", res_has_facts.stdout)

        # 4. Harvest with --force overrides active facts check
        res_force = self._run_te(["harvest", "--force"])
        self.assertEqual(res_force.returncode, 0)
        self.assertIn("To harvest 5-8 golden architectural facts", res_force.stdout)


if __name__ == "__main__":
    unittest.main()
