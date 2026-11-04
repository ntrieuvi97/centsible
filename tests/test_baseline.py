#!/usr/bin/env python3
"""Transcript analysis (Claude Code format) and before/after exploration baseline."""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.cli import analyze_transcript, claude_project_dir, compare_baseline  # noqa: E402


def use(i, name):
    return {"type": "assistant", "timestamp": "2026-11-01T00:00:00Z",
            "message": {"content": [{"type": "tool_use", "id": i, "name": name, "input": {}}]}}


def result(i, text="x", err=False):
    return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": i, "content": text, "is_error": err}]}}


def write(path, steps):
    path.write_text("\n".join(json.dumps(s) for s in steps), encoding="utf-8")


def session(path, ts, n_explore):
    steps = [{"type": "user", "timestamp": ts, "message": {"content": "do it"}}]
    for k in range(n_explore):
        steps += [use(f"r{k}", "Read"), result(f"r{k}", "y" * 40)]  # ~10 tokens each
    steps += [use("e", "Edit"), result("e")]
    write(path, steps)


class TestTranscript(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_claude_code_format(self):
        p = self.dir / "a.jsonl"
        write(p, [
            {"type": "user", "timestamp": "2026-11-01T00:00:00Z", "message": {"content": "hi"}},
            use("1", "Grep"), result("1", "z" * 40),
            use("2", "Bash"), result("2", "boom", err=True),
            use("3", "Edit"), result("3"),
            {**use("4", "Read"), "isSidechain": True},
        ])
        r = analyze_transcript(p)
        self.assertEqual(r["user_turns"], 1)  # tool_result carriers are not user turns
        self.assertEqual(r["tool_calls"], 3)  # sidechain skipped
        self.assertEqual(r["failed_calls"], 1)
        self.assertEqual(r["calls_before_first_edit"], 2)
        self.assertEqual(r["tokens_before_first_edit"], 10 + 1)  # 40 bytes -> 10 tokens, "boom" -> 1
        self.assertEqual(r["edit_calls"], 1)
        self.assertEqual(r["started_at"], "2026-11-01T00:00:00Z")

    def test_baseline_split_and_savings(self):
        for i in range(5):
            session(self.dir / f"b{i}.jsonl", "2026-11-01T00:00:00Z", 10)
            session(self.dir / f"a{i}.jsonl", "2026-11-05T00:00:00Z", 4)
        session(self.dir / "noedit.jsonl", "2026-11-05T00:00:00Z", 0)
        write(self.dir / "qa.jsonl", [use("1", "Read"), result("1")])  # no edit -> ignored
        since = datetime(2026, 11, 3, tzinfo=timezone.utc)
        r = compare_baseline(self.dir, since)
        self.assertTrue(r["enough_data"])
        self.assertEqual((r["before"]["sessions"], r["after"]["sessions"]), (5, 6))
        self.assertEqual(r["saved_calls_per_session"], 6)  # median 10 -> 4
        self.assertGreater(r["saved_tokens_per_session"], 0)

    def test_not_enough_data(self):
        session(self.dir / "b.jsonl", "2026-11-01T00:00:00Z", 3)
        r = compare_baseline(self.dir, datetime(2026, 11, 3, tzinfo=timezone.utc))
        self.assertFalse(r["enough_data"])
        self.assertNotIn("saved_tokens_per_session", r)


class ProjectDirTest(unittest.TestCase):
    def test_encodes_cwd_like_claude_code(self):
        d = claude_project_dir(Path("/Users/a.b/My Repo/x"))
        self.assertEqual(d.name, "-Users-a-b-My-Repo-x")


if __name__ == "__main__":
    unittest.main()
