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
        custom_env = dict(subprocess.os.environ)
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


if __name__ == "__main__":
    unittest.main()
