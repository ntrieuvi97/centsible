"""Session budget: usage parsing, soft warning, hard stop."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.budget import evaluate_budget, read_transcript_usage, usage_tokens  # noqa: E402
from core.loop_guard import LoopGuard  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def _assistant(mid, inp=100, out=50, cache_read=0):
    return json.dumps({"type": "assistant", "message": {"id": mid, "usage": {
        "input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cache_read}}})


class TestUsageParsing(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "s.jsonl"

    def tearDown(self):
        self.tmp.cleanup()

    def test_cache_reads_are_discounted(self):
        self.assertEqual(usage_tokens({"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 1000}), 115)
        self.assertEqual(usage_tokens({"input_tokens": "bad"}), 0)

    def test_incremental_and_deduplicated_by_message_id(self):
        # One message streamed as two lines (same id, same usage) is counted once.
        self.path.write_text("\n".join([_assistant("m1"), _assistant("m1"), '{"type":"user"}']) + "\n")
        delta, off, seen = read_transcript_usage(self.path, 0, [])
        self.assertEqual(delta, 150)
        with open(self.path, "a") as f:
            f.write(_assistant("m2", 10, 10) + "\n" + _assistant("m3", 999, 0))  # m3 line not finished yet
        delta2, off2, seen2 = read_transcript_usage(self.path, off, seen)
        self.assertEqual(delta2, 20)
        self.assertGreater(off2, off)
        with open(self.path, "a") as f:
            f.write("\n")
        self.assertEqual(read_transcript_usage(self.path, off2, seen2)[0], 999)

    def test_missing_file_is_harmless(self):
        self.assertEqual(read_transcript_usage(self.path, 5, ["x"]), (0, 5, ["x"]))

    def test_evaluate_levels(self):
        self.assertEqual(evaluate_budget(10, True, 0, 100, 0), (None, None))
        level, msg = evaluate_budget(80, True, 0, 100, 0)
        self.assertEqual(level, "warn")
        level, msg = evaluate_budget(5, False, 10, 100, 10)
        self.assertEqual(level, "stop")
        self.assertIn("session_budget_calls", msg)


class TestLoopGuardBudget(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_off_by_default(self):
        g = LoopGuard("budget-off")
        for i in range(30):
            g.record_post_tool_use({"name": "Read", "args": {"file_path": f"f{i}"}}, None, io_tokens=10_000)
        self.assertEqual(g.check_pre_tool_use({"name": "Read", "args": {"file_path": "new"}}), ("allow", None))

    def test_call_budget_warns_once_then_blocks_every_call(self):

        os.environ["CENTSIBLE_SESSION_BUDGET_CALLS"] = "5"
        try:
            g = LoopGuard("budget-calls")
            reasons = []
            for i in range(5):
                decision, reason = g.check_pre_tool_use({"name": "Read", "args": {"file_path": f"f{i}"}})
                self.assertEqual(decision, "allow")
                reasons.append(reason)
                g.record_post_tool_use({"name": "Read", "args": {"file_path": f"f{i}"}}, None)
            warns = [r for r in reasons if r and "80%" in r]
            self.assertEqual(len(warns), 1)  # warned at 4/5 calls, only once
            for i in range(2):
                decision, reason = g.check_pre_tool_use({"name": "Read", "args": {"file_path": f"g{i}"}})
                self.assertEqual(decision, "deny")
                self.assertIn("5/5 tool calls", reason)
        finally:
            del os.environ["CENTSIBLE_SESSION_BUDGET_CALLS"]

    def test_estimated_tokens_used_without_transcript(self):

        os.environ["CENTSIBLE_SESSION_BUDGET_TOKENS"] = "1000"
        try:
            g = LoopGuard("budget-est")
            g.record_post_tool_use({"name": "terminal", "args": {"command": "ls"}}, None, io_tokens=1200)
            decision, reason = g.check_pre_tool_use({"name": "terminal", "args": {"command": "pwd"}})
            self.assertEqual(decision, "deny")
            self.assertIn("estimated from tool I/O only", reason)
        finally:
            del os.environ["CENTSIBLE_SESSION_BUDGET_TOKENS"]


class TestClaudeBudgetHook(unittest.TestCase):
    def test_measured_transcript_budget_blocks_with_exit_2(self):
        with tempfile.TemporaryDirectory() as d:
            transcript = Path(d) / "t.jsonl"
            transcript.write_text(_assistant("m1", 3000, 500) + "\n")
            env = {k: v for k, v in os.environ.items() if not k.startswith("CENTSIBLE_")}
            env.update({"CENTSIBLE_HOME": d, "CENTSIBLE_SESSION_BUDGET_TOKENS": "2000"})
            payload = {"session_id": "budget-claude", "cwd": d, "tool_name": "Bash",
                       "tool_input": {"command": "ls"}, "transcript_path": str(transcript)}
            res = subprocess.run([sys.executable, str(REPO_ROOT / "core" / "cli.py"), "hook", "claude-pre-tool"],
                                 input=json.dumps(payload), capture_output=True, text=True, encoding="utf-8", cwd=d, env=env)
            self.assertEqual(res.returncode, 2)
            self.assertIn("3,500/2,000 tokens (measured from transcript)", res.stderr)


if __name__ == "__main__":
    unittest.main()
