#!/usr/bin/env python3
"""
Unit tests for Fact Attribution, LoopGuard Error Trace Savings, and First-Try Command Rate.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from core.facts_engine import (
    DEFAULT_SAVED_TOKENS_PER_HIT,
    DEFAULT_SAVED_TOKENS_PER_LOOP_STOP,
    FactsEngine,
    check_fact_attribution,
    extract_fact_prescriptions,
)
from core.loop_guard import LoopGuard
from core.cli import analyze_transcript


class TestAttributionAndMetrics(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.storage_path = Path(self.tmp_dir.name) / "facts.jsonl"
        self.engine = FactsEngine(self.storage_path)
        self.conv_id = f"test_attribution_{id(self)}"
        self.guard = LoopGuard(conversation_id=self.conv_id, max_consecutive_errors=2, max_identical_calls=2)

    def tearDown(self):
        self.guard.reset()
        self.tmp_dir.cleanup()

    def test_extract_fact_prescriptions(self):
        fact_text = "Append --compact to Bruno commands. Status is 'IN PROCESS'. Use '111111' instead of 'wrong'."
        flags, quoted = extract_fact_prescriptions(fact_text)
        self.assertIn("--compact", flags)
        self.assertIn("IN PROCESS", quoted)
        # 'wrong' is after 'instead of', so it should not be prescribed
        self.assertNotIn("wrong", quoted)

    def test_check_fact_attribution_flags(self):
        fact = {
            "id": "f_condense",
            "pattern": "bruno",
            "fact": "Append --compact to Bruno commands to save 80% JSON tokens.",
        }
        # Flag present
        tool_call_match = {
            "name": "run_command",
            "args": {"CommandLine": "bruno run req.bru --compact"},
        }
        self.assertTrue(check_fact_attribution(fact, tool_call_match))

        # Flag missing, but pattern matches on success
        tool_call_no_flag = {
            "name": "run_command",
            "args": {"CommandLine": "bruno run req.bru"},
        }
        self.assertTrue(check_fact_attribution(fact, tool_call_no_flag, is_success=True))
        self.assertFalse(check_fact_attribution(fact, tool_call_no_flag, is_success=False))

    def test_check_fact_attribution_quoted_values(self):
        fact = {
            "id": "f_passcode",
            "pattern": "passcode|login",
            "fact": "staging passcode '111111' là mật khẩu cố định (KHÔNG phải OTP).",
        }
        tool_call = {
            "name": "run_command",
            "args": {"CommandLine": "python3 login.py --passcode 111111"},
        }
        self.assertTrue(check_fact_attribution(fact, tool_call))

    def test_loop_guard_records_fact_attribution(self):
        fact = self.engine.add_fact(
            pattern="pytest",
            fact="Run pytest with '-q' flag.",
            fact_id="fact_pytest_q",
            force=True,
        )
        self.guard.record_injected_facts(["fact_pytest_q"])

        # Tool call uses -q
        tool_call = {
            "name": "run_command",
            "args": {"CommandLine": "pytest tests/ -q"},
        }
        self.guard.record_post_tool_use(tool_call, error=None, engine=self.engine)

        summary = self.engine.get_metrics_summary()
        self.assertEqual(summary["attributed_hits"], 1)
        self.assertGreater(summary["attributed_saved_tokens"], 0)

        # Stored fact also tracks attributed_hits
        saved_fact = self.engine.get_fact("fact_pytest_q")
        self.assertIsNotNone(saved_fact)
        self.assertEqual(saved_fact.get("attributed_hits"), 1)

    def test_loop_guard_measured_error_trace_savings(self):
        # Generate a large traceback error (~1000 tokens)
        long_traceback = "Traceback (most recent call last):\n" + ("  File 'foo.py', line 1, in bar\n" * 150) + "RuntimeError: crash\n"

        tool_call_1 = {"name": "run_command", "args": {"CommandLine": "crash_cmd"}}
        tool_call_2 = {"name": "run_command", "args": {"CommandLine": "crash_cmd"}}

        # Call 1 fails with long error trace
        self.guard.record_post_tool_use(tool_call_1, error=long_traceback, engine=self.engine)

        # Call 2 pre-check trips loop guard (max_consecutive_errors reached)
        self.guard.record_post_tool_use(tool_call_2, error=long_traceback, engine=self.engine)
        decision, reason = self.guard.check_pre_tool_use(tool_call_2, engine=self.engine)

        self.assertEqual(decision, "ask")
        self.assertIn("[LoopGuard Alert]", reason)

        # Measured savings should be significantly higher than default because of large traceback
        summary = self.engine.get_metrics_summary()
        self.assertEqual(summary["loop_interventions"], 1)
        self.assertGreater(summary["saved_loop_tokens"], DEFAULT_SAVED_TOKENS_PER_LOOP_STOP)

    def test_first_try_command_rate_tracking(self):
        # 1st cmd call: success on 1st try
        cmd1 = {"name": "run_command", "args": {"CommandLine": "ls"}}
        self.guard.record_post_tool_use(cmd1, error=None, engine=self.engine)

        # 2nd cmd call: fails
        cmd2 = {"name": "run_command", "args": {"CommandLine": "invalid_cmd"}}
        self.guard.record_post_tool_use(cmd2, error="command not found", engine=self.engine)

        # 3rd non-cmd call: read_file success
        tool_read = {"name": "read_file", "args": {"Path": "file.txt"}}
        self.guard.record_post_tool_use(tool_read, error=None, engine=self.engine)

        summary = self.engine.get_metrics_summary()
        self.assertEqual(summary["command_calls_total"], 2)
        self.assertEqual(summary["command_calls_failed"], 1)
        self.assertEqual(summary["first_try_cmd_successes"], 1)
        self.assertEqual(summary["first_try_cmd_rate"], 50.0)

    def test_transcript_wasted_retry_tokens(self):
        transcript_file = Path(self.tmp_dir.name) / "transcript.jsonl"
        steps = [
            {"type": "user", "message": {"content": "do task"}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "cmd"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t1", "content": "error 1", "is_error": True}]}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "id": "t2", "name": "Bash", "input": {"command": "cmd"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "t2", "content": "error 2 with long stacktrace " * 20, "is_error": True}]}},
        ]
        transcript_file.write_text("\n".join(json.dumps(s) for s in steps), encoding="utf-8")

        res = analyze_transcript(transcript_file)
        self.assertEqual(res["retry_streaks"], 1)
        self.assertEqual(res["wasted_retry_calls"], 1)
        self.assertGreater(res["wasted_retry_tokens"], 10)

    def test_jit_inject_mode_configuration(self):
        from core.loop_guard import resolve_inject_mode
        from unittest import mock
        # Default is smart hybrid when no config file exists
        with tempfile.TemporaryDirectory() as home:
            with mock.patch.dict("os.environ", {"CENTSIBLE_HOME": home}, clear=False):
                self.assertEqual(resolve_inject_mode(), "smart")
        # Can be overridden via env
        with mock.patch.dict("os.environ", {"CENTSIBLE_INJECT_MODE": "jit"}):
            self.assertEqual(resolve_inject_mode(), "jit")
        with mock.patch.dict("os.environ", {"CENTSIBLE_INJECT_MODE": "eager"}):
            self.assertEqual(resolve_inject_mode(), "eager")
        with mock.patch.dict("os.environ", {"CENTSIBLE_INJECT_MODE": "off"}):
            self.assertEqual(resolve_inject_mode(), "off")

    def test_smart_hybrid_domain_matching_and_capping(self):
        from core.facts_engine import TOOL_CATEGORIES
        # Add 5 domain facts and 1 tool fact, all matching prompt 'order'
        for i in range(5):
            self.engine.add_fact(
                pattern=f"order_{i}|order",
                fact=f"Domain business rule number {i} for orders",
                category="database",
                fact_id=f"domain_{i}",
                force=True,
            )
        self.engine.add_fact(
            pattern="order_tool|order",
            fact="Run order tool with --verbose",
            category="tool",
            fact_id="tool_fact",
            force=True,
        )

        # In smart hybrid mode: TOOL_CATEGORIES are excluded, max_facts=3
        matched = self.engine.match(
            "check order status",
            record_hits=True,
            max_facts=3,
            exclude_categories=TOOL_CATEGORIES,
        )

        # 1. Total matched must be capped at 3
        self.assertEqual(len(matched), 3)
        # 2. Tool fact must NOT be in prompt injection
        self.assertNotIn("tool_fact", [f["id"] for f in matched])
        # 3. All returned are domain database facts
        self.assertTrue(all(f["category"] == "database" for f in matched))


if __name__ == "__main__":
    unittest.main()
