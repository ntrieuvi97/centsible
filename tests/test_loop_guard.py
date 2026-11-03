#!/usr/bin/env python3
"""
Unit tests for LoopGuard.
"""

import tempfile
import unittest
from pathlib import Path

from core.loop_guard import LoopGuard, get_state_path


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


if __name__ == "__main__":
    unittest.main()
