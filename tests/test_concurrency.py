#!/usr/bin/env python3
"""Parallel hooks must not lose metrics or loop-guard state (read-modify-write under file lock)."""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from core.facts_engine import FactsEngine
from core.loop_guard import LoopGuard

THREADS, PER_THREAD = 8, 25


def _run_parallel(fn):
    workers = [threading.Thread(target=lambda: [fn() for _ in range(PER_THREAD)]) for _ in range(THREADS)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()


class TestConcurrency(unittest.TestCase):
    def test_metrics_increments_not_lost(self):
        with tempfile.TemporaryDirectory() as d:
            engine = FactsEngine(Path(d) / "facts.jsonl")
            _run_parallel(lambda: engine.update_metrics(tool_calls_total=1))
            self.assertEqual(engine.load_metrics()["tool_calls_total"], THREADS * PER_THREAD)

    def test_loop_guard_state_not_lost(self):
        with tempfile.TemporaryDirectory() as d, mock.patch("tempfile.gettempdir", return_value=d):
            guard = LoopGuard("race-session")
            _run_parallel(lambda: guard.record_post_tool_use({"name": "Read", "args": {"file_path": "x"}}))
            self.assertEqual(guard._load_state()["session_tool_calls"], THREADS * PER_THREAD)


if __name__ == "__main__":
    unittest.main()
