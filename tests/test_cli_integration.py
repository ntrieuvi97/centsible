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
import uuid
from pathlib import Path

from core.loop_guard import get_state_path


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

    def test_config_reflect_cli(self):
        env = {"CENTSIBLE_HOME": self.temp_dir.name, "CENTSIBLE_REFLECT": "", "CENTSIBLE_AUTO_LEARN_NUDGE": ""}
        # 1. Default reflect is inline
        res = self._run_te(["config", "reflect"], env=env)
        self.assertEqual(res.returncode, 0)
        self.assertIn("reflect = inline", res.stdout)
        self.assertEqual(self._run_te(["config", "get", "reflect"], env=env).stdout.strip(), "inline")

        # 2. Set reflect hook
        res_hook = self._run_te(["config", "reflect", "hook"], env=env)
        self.assertEqual(res_hook.returncode, 0)
        self.assertIn("reflect = hook", res_hook.stdout)
        self.assertEqual(self._run_te(["config", "get", "reflect"], env=env).stdout.strip(), "hook")

        # 3. Set reflect off
        res_off = self._run_te(["config", "reflect", "off"], env=env)
        self.assertEqual(res_off.returncode, 0)
        self.assertIn("reflect = off", res_off.stdout)
        self.assertEqual(self._run_te(["config", "get", "reflect"], env=env).stdout.strip(), "off")

        # 4. Set reflect on (maps to inline)
        res_on = self._run_te(["config", "reflect", "on"], env=env)
        self.assertEqual(res_on.returncode, 0)
        self.assertIn("reflect = inline", res_on.stdout)

        # 5. Invalid choice rejected
        self.assertNotEqual(self._run_te(["config", "reflect", "maybe"], env=env).returncode, 0)

        # 6. Unset reflect reverts to inline
        res_unset = self._run_te(["config", "unset", "reflect"], env=env)
        self.assertEqual(res_unset.returncode, 0)
        self.assertIn("reflect = inline", res_unset.stdout)

        # 7. Overridden by env var
        env_override = dict(env, CENTSIBLE_REFLECT="hook")
        res_env = self._run_te(["config", "list"], env=env_override)
        self.assertIn("reflect = hook (overridden by env CENTSIBLE_REFLECT)", res_env.stdout)

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

        # 5. Prune command
        res_prune = self._run_te(["facts", "prune"])
        self.assertEqual(res_prune.returncode, 0)
        self.assertIn("No stale or zero-utility facts to prune", res_prune.stdout)

    def test_pre_invocation_hook_contract(self):
        # Seed a fact
        self._run_te([
            "facts", "add",
            "--pattern", "bruno",
            "--force",
            "--fact", "Use --compact flag on UAT",
            "--category", "always",
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
        self.assertIn("Use --compact flag on UAT", data["injectSteps"][0]["ephemeralMessage"])

    def _pre_tool(self, conversation_id, **extra):
        payload = {
            "toolCall": {"name": "exec", "args": {"command": "bruno run uat"}},
            "conversationId": conversation_id,
            "workspacePaths": [self.temp_dir.name],
            **extra,
        }
        res = self._run_te(["hook", "pre-tool"], stdin_data=json.dumps(payload))
        self.assertEqual(res.returncode, 0)
        return json.loads(res.stdout)

    def test_pre_tool_hint_blocks_by_default_but_not_when_host_can_attach_it(self):
        self._run_te([
            "facts", "add", "--pattern", "bruno.{0,30}(run|uat)",
            "--fact", "Append --compact to bruno commands", "--category", "tool", "--id", "f-bruno",
        ])
        run_id = uuid.uuid4().hex  # guard state outlives the temp dir, so ids must be unique per run
        blocking = self._pre_tool(f"hint_default_{run_id}")
        self.assertEqual(blocking["decision"], "ask")  # Antigravity cannot inject context, so it shows the hint by asking
        self.assertIn("--compact", blocking["reason"])

        attached = self._pre_tool(f"hint_non_blocking_{run_id}", blockHints=False)
        self.assertEqual(attached["decision"], "allow")
        self.assertIn("--compact", attached["reason"])

    def test_pre_tool_loop_guard_still_blocks_when_hints_are_non_blocking(self):
        loop_id = f"loop_non_blocking_{uuid.uuid4().hex}"
        for _ in range(4):  # default limit: 4 identical calls
            res = self._pre_tool(loop_id, blockHints=False)
        self.assertEqual(res["decision"], "ask")
        self.assertIn("LoopGuard", res["reason"])

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

    def test_cli_install_and_module_execution(self):
        # Test centsible install --dry-run
        res = self._run_te(["install", "--dry-run"])
        self.assertEqual(res.returncode, 0)
        self.assertIn("=== Centsible CLI Installation 🪙 ===", res.stdout)

        # Test python3 -m centsible status
        res_m = subprocess.run(
            [sys.executable, "-m", "centsible", "status"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        self.assertEqual(res_m.returncode, 0)
        self.assertIn("=== Centsible Status 🪙 ===", res_m.stdout)

    def test_stats_all_sums_projects(self):
        import json as _json
        for name, calls in (("a-1", 3), ("b-2", 4)):
            d = Path(self.temp_dir.name) / "projects" / name
            d.mkdir(parents=True)
            (d / "metrics.json").write_text(_json.dumps({"tool_calls_total": calls, "first_try_successes": calls}))
        res = self._run_te(["stats", "--all", "--json"])
        self.assertEqual(res.returncode, 0)
        self.assertEqual(_json.loads(res.stdout)["metrics"]["tool_calls_total"], 7)

    def test_stats_all_does_not_double_count_migrated_facts(self):
        import json as _json
        home = Path(self.temp_dir.name)
        (home / "metrics.json").write_text(_json.dumps({"fact_hits": 10, "saved_discovery_tokens": 15000, "tool_calls_total": 5}))
        d = home / "projects" / "a-1"
        d.mkdir(parents=True)
        (d / "metrics.json").write_text(_json.dumps({"tool_calls_total": 2}))
        (d / "facts.jsonl").write_text(_json.dumps({"id": "f1", "fact": "x", "hits": 10, "saved_tokens": 15000}) + "\n")
        m = _json.loads(self._run_te(["stats", "--all", "--json"]).stdout)["metrics"]
        self.assertEqual((m["fact_hits"], m["saved_discovery_tokens"], m["tool_calls_total"]), (10, 15000, 7))

    def test_stats_and_transcript_analysis(self):
        # 1. Seed a fact and trigger a hit
        self._run_te([
            "facts", "add",
            "--pattern", "pytest",
            "--force",
            "--fact", "Use pytest -q",
            "--category", "always",
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
            "--force",
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

    def test_antigravity_stop_hook_self_learning(self):
        cid = f"agy_stop_{id(self)}"

        def post_tool(tool_name, cmd=None):
            payload = {
                "conversationId": cid,
                "workspacePaths": [self.temp_dir.name],
                "toolCall": {"name": tool_name, "args": {"CommandLine": cmd} if cmd else {}},
            }
            return self._run_te(["hook", "post-tool"], json.dumps(payload))

        def stop_hook(term_reason="NO_TOOL_CALL", env=None):
            payload = {
                "conversationId": cid,
                "workspacePaths": [self.temp_dir.name],
                "terminationReason": term_reason,
            }
            return self._run_te(["hook", "stop"], json.dumps(payload), env=env)

        # 1. No tool calls yet -> stop hook returns empty {}
        res = stop_hook()
        self.assertEqual(json.loads(res.stdout), {})

        # 2. Mutating tool executed (e.g. edit_file) in default 'inline' mode
        post_tool("edit_file")
        # In default 'inline' mode, Stop hook does NOT block (no extra turns, preserves answer)
        self.assertEqual(json.loads(stop_hook("NO_TOOL_CALL").stdout), {})

        # 3. In 'hook' mode, Stop hook blocks once with continue
        env_hook = {"CENTSIBLE_REFLECT": "hook"}
        # Non-clean termination (USER_CANCELED or ERROR) must never block
        self.assertEqual(json.loads(stop_hook("USER_CANCELED", env=env_hook).stdout), {})
        self.assertEqual(json.loads(stop_hook("ERROR", env=env_hook).stdout), {})

        # Clean termination NO_TOOL_CALL in hook mode -> nudges agent with continue
        res_stop = stop_hook("NO_TOOL_CALL", env=env_hook)
        out = json.loads(res_stop.stdout)
        self.assertEqual(out.get("decision"), "continue")
        self.assertIn("facts add", out.get("reason", ""))

        # 4. Second stop call in same session is consumed (one-shot guarantee)
        res_stop_again = stop_hook("NO_TOOL_CALL", env=env_hook)
        self.assertEqual(json.loads(res_stop_again.stdout), {})

        get_state_path(cid).unlink(missing_ok=True)

    def test_share_cli(self):
        res = self._run_te(["share", "--json"])
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertIn("share_card", data)
        self.assertIn("badge_markdown", data)
        self.assertIn("twitter_share_url", data)

        res_badge = self._run_te(["share", "--badge"])
        self.assertEqual(res_badge.returncode, 0)
        self.assertIn("img.shields.io", res_badge.stdout)

        res_card = self._run_te(["share", "--card"])
        self.assertEqual(res_card.returncode, 0)
        self.assertIn("Centsible Token & Cost Savings Report", res_card.stdout)

    def test_export_cli(self):
        # Add a test fact first
        res_add = self._run_te(["facts", "add", "--pattern", "pytest.{0,20}flag", "--fact", "Use pytest -q flag"])
        self.assertEqual(res_add.returncode, 0)

        # Test export markdown to stdout
        res_md = self._run_te(["export", "--target", "markdown", "--stdout"])
        self.assertEqual(res_md.returncode, 0)
        self.assertIn("Use pytest -q flag", res_md.stdout)

        # Test export cursor to stdout
        res_cursor = self._run_te(["export", "--target", "cursor", "--stdout"])
        self.assertEqual(res_cursor.returncode, 0)
        self.assertIn("alwaysApply: true", res_cursor.stdout)
        self.assertIn("Use pytest -q flag", res_cursor.stdout)

        # Test export aider to stdout
        res_aider = self._run_te(["export", "--target", "aider", "--stdout"])
        self.assertEqual(res_aider.returncode, 0)
        self.assertIn("Repository Conventions", res_aider.stdout)

        # Test export json to stdout
        res_json = self._run_te(["export", "--target", "json", "--stdout"])
        self.assertEqual(res_json.returncode, 0)
        facts = json.loads(res_json.stdout)
        self.assertTrue(any("pytest" in f.get("pattern", "") for f in facts))


if __name__ == "__main__":
    unittest.main()
