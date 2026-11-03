#!/usr/bin/env python3
"""
Unit tests for LoopGuard.
"""

import os
import tempfile
import unittest
from pathlib import Path

from core.facts_engine import FactsEngine
from core.loop_guard import LoopGuard, extract_error_from_payload, get_state_path


class TestLoopGuard(unittest.TestCase):
    def setUp(self):
        self.conv_id = f"test_conv_{id(self)}"
        self.guard = LoopGuard(
            conversation_id=self.conv_id,
            max_identical_calls=3,
            max_consecutive_errors=2,
        )

    def tearDown(self):
        self.guard.reset()

    def test_thresholds_from_env(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as home:
            env = {
                "CENTSIBLE_HOME": home,
                "CENTSIBLE_MAX_CONSECUTIVE_ERRORS": "5",
                "CENTSIBLE_MAX_IDENTICAL_CALLS": "bogus",
            }
            with mock.patch.dict("os.environ", env):
                g = LoopGuard(conversation_id=self.conv_id)
            self.assertEqual(g.max_consecutive_errors, 5)
            self.assertEqual(g.max_identical_calls, 4)

    def test_thresholds_from_config_file(self):
        from unittest import mock
        from core.loop_guard import save_config
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict("os.environ", {"CENTSIBLE_HOME": home}, clear=False):
                os.environ.pop("CENTSIBLE_MAX_CONSECUTIVE_ERRORS", None)
                save_config({"max_consecutive_errors": 4, "max_identical_calls": 0})
                g = LoopGuard(conversation_id=self.conv_id)
            self.assertEqual(g.max_consecutive_errors, 4)
            self.assertEqual(g.max_identical_calls, 4)

    def test_mode_presets_and_overrides(self):
        from unittest import mock
        from core.loop_guard import save_config
        with tempfile.TemporaryDirectory() as home:
            clean = {k: v for k, v in os.environ.items() if not k.startswith("CENTSIBLE_")}
            clean["CENTSIBLE_HOME"] = home
            with mock.patch.dict("os.environ", clean, clear=True):
                g = LoopGuard(conversation_id=self.conv_id)
                self.assertEqual((g.max_consecutive_errors, g.max_identical_calls), (3, 4))
                save_config({"mode": "ultra"})
                g = LoopGuard(conversation_id=self.conv_id)
                self.assertEqual((g.max_consecutive_errors, g.max_identical_calls), (2, 3))
                save_config({"mode": "weak", "max_identical_calls": 7})
                g = LoopGuard(conversation_id=self.conv_id)
                self.assertEqual((g.max_consecutive_errors, g.max_identical_calls), (4, 7))
                os.environ["CENTSIBLE_LOOP_MODE"] = "ultra"
                g = LoopGuard(conversation_id=self.conv_id)
                self.assertEqual(g.max_consecutive_errors, 2)
                os.environ["CENTSIBLE_LOOP_MODE"] = "bogus"
                g = LoopGuard(conversation_id=self.conv_id)
                self.assertEqual(g.max_consecutive_errors, 4)

    def test_allow_first_and_second_identical_call(self):
        tool_call = {
            "name": "run_command",
            "args": {"CommandLine": "pytest tests/"}
        }

        # First call
        decision, reason = self.guard.check_pre_tool_use(tool_call)
        self.assertEqual(decision, "allow")
        self.assertIsNone(reason)

        # Second call
        decision, reason = self.guard.check_pre_tool_use(tool_call)
        self.assertEqual(decision, "allow")
        self.assertIsNone(reason)

        # Third identical call triggers loop guard intervention ('ask')
        decision, reason = self.guard.check_pre_tool_use(tool_call)
        self.assertEqual(decision, "ask")
        self.assertIn("[LoopGuard Alert]", reason)
        self.assertIn("3 times consecutively", reason)

    def test_consecutive_error_circuit_breaker(self):
        tool_call_1 = {"name": "run_command", "args": {"CommandLine": "bad_cmd_1"}}
        tool_call_2 = {"name": "run_command", "args": {"CommandLine": "bad_cmd_2"}}
        tool_call_3 = {"name": "run_command", "args": {"CommandLine": "bad_cmd_3"}}

        # Call 1 fails
        self.guard.check_pre_tool_use(tool_call_1)
        self.guard.record_post_tool_use(tool_call_1, error="exit code 1")

        # Call 2 fails
        self.guard.check_pre_tool_use(tool_call_2)
        self.guard.record_post_tool_use(tool_call_2, error="exit code 127")

        # Call 3 should be stopped by circuit breaker (2 consecutive errors)
        decision, reason = self.guard.check_pre_tool_use(tool_call_3)
        self.assertEqual(decision, "ask")
        self.assertIn("consecutive failures detected", reason)

        # Blocked call never runs, so the breaker must release itself: next call goes through
        decision, _ = self.guard.check_pre_tool_use(tool_call_3)
        self.assertEqual(decision, "allow")

    def test_recovery_after_success(self):
        tool_call_1 = {"name": "run_command", "args": {"CommandLine": "bad_cmd"}}
        tool_call_2 = {"name": "run_command", "args": {"CommandLine": "good_cmd"}}
        tool_call_3 = {"name": "run_command", "args": {"CommandLine": "next_cmd"}}

        # Error on call 1
        self.guard.check_pre_tool_use(tool_call_1)
        self.guard.record_post_tool_use(tool_call_1, error="failed")

        # Success on call 2
        self.guard.check_pre_tool_use(tool_call_2)
        self.guard.record_post_tool_use(tool_call_2, error=None)

        # Call 3 should be allowed because error count was reset
        decision, reason = self.guard.check_pre_tool_use(tool_call_3)
        self.assertEqual(decision, "allow")

    def test_auto_learn_survives_unrelated_tool_between_failure_and_retry(self):
        engine = FactsEngine(Path(tempfile.mkdtemp()) / "facts.jsonl")
        failed = {"name": "Bash", "args": {"command": "pytest tests/"}}
        read = {"name": "Read", "args": {"file_path": "/x"}}
        edit = {"name": "Edit", "args": {"file_path": "/x"}}
        retry = {"name": "Bash", "args": {"command": "python3 -m pytest tests/ -q"}}

        # 1. File Edit between failure and retry clears last_failed_cmd (bug fix, not CLI quirk)
        self.guard.record_post_tool_use(failed, error="exit code 1", engine=engine)
        self.guard.record_post_tool_use(edit, error=None, engine=engine)
        self.guard.record_post_tool_use(retry, error=None, engine=engine)
        self.assertEqual(engine.list_facts(), [])

        # 2. Read-only tool between failure and retry preserves last_failed_cmd
        self.guard.record_post_tool_use(failed, error="exit code 1", engine=engine)
        self.guard.record_post_tool_use(read, error=None, engine=engine)
        self.guard.record_post_tool_use(retry, error=None, engine=engine)

        facts = engine.list_facts()
        self.assertEqual([f["category"] for f in facts], ["auto-learned"])
        self.assertIsNone(self.guard._load_state()["last_failed_cmd"])

    def test_command_normalization_catches_cosmetic_retries(self):
        # Changing toolSummary, WaitMsBeforeAsync, or -v/--tb=short should still count as identical
        call_1 = {"name": "run_command", "args": {"CommandLine": "pytest tests/a.py", "toolSummary": "Run 1", "WaitMsBeforeAsync": 5000}}
        call_2 = {"name": "run_command", "args": {"CommandLine": "pytest  tests/a.py -v", "toolSummary": "Run 2", "WaitMsBeforeAsync": 3000}}
        call_3 = {"name": "run_command", "args": {"CommandLine": "pytest tests/a.py --tb=short -q", "toolSummary": "Run 3"}}

        self.assertEqual(self.guard.check_pre_tool_use(call_1)[0], "allow")
        self.assertEqual(self.guard.check_pre_tool_use(call_2)[0], "allow")
        decision, reason = self.guard.check_pre_tool_use(call_3)
        self.assertEqual(decision, "ask")
        self.assertIn("[LoopGuard Alert]", reason)

    def test_extract_error_from_payload_variants(self):
        self.assertEqual(extract_error_from_payload({"exitCode": 2}), "exit code 2")
        self.assertEqual(
            extract_error_from_payload({"output": "Done.\nCommand exited with code 127.\n"}),
            "exit code 127",
        )
        self.assertIsNone(extract_error_from_payload({"output": "Command exited with code 0."}))

    def test_pre_tool_fact_hint_when_uninjected(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = FactsEngine(Path(tmp) / "facts.jsonl")
            engine.add_fact("bruno", "Append --compact to bruno commands", fact_id="f-bruno")

            # Agent calls bruno without --compact when prompt did not inject f-bruno
            call_missing = {"name": "run_command", "args": {"CommandLine": "bruno run suite"}}
            decision, reason = self.guard.check_pre_tool_use(call_missing, engine=engine)
            self.assertEqual(decision, "ask")
            self.assertIn("[Centsible Micro-Skill Hint]", reason)
            self.assertIn("--compact", reason)

            # File/module gotcha returns 'allow' with hint (smooth injection without blocking Edit/Read)
            engine.add_fact(
                "compositor.py",
                "Keep floating_cards intact in AnimeLayerCompositor",
                category="gotcha",
                fact_id="f-gotcha",
            )
            edit_call = {"name": "Edit", "args": {"file_path": "/app/src/compositor.py"}}
            decision_g, reason_g = self.guard.check_pre_tool_use(edit_call, engine=engine)
            self.assertEqual(decision_g, "allow")
            self.assertIn("floating_cards", reason_g)

            # Second Edit in same session does not re-inject the same gotcha
            decision_g2, reason_g2 = self.guard.check_pre_tool_use(edit_call, engine=engine)
            self.assertEqual(decision_g2, "allow")
            self.assertIsNone(reason_g2)


if __name__ == "__main__":
    unittest.main()
