#!/usr/bin/env python3
"""
Unit tests for FactsEngine.
"""

import os
import tempfile
import unittest
from pathlib import Path

from core.facts_engine import FactsEngine, get_default_facts_path


class TestFactsEngine(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_path = Path(self.temp_dir.name) / "facts.jsonl"
        self.engine = FactsEngine(self.storage_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_add_and_list_facts(self):
        self.engine.add_fact("pytest|test", "Run pytest with -q flag", category="test", fact_id="test-1")
        facts = self.engine.list_facts()
        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["id"], "test-1")
        self.assertEqual(facts[0]["pattern"], "pytest|test")
        self.assertEqual(facts[0]["fact"], "Run pytest with -q flag")
        self.assertEqual(facts[0]["hits"], 0)

    def test_invalid_regex_raises_error(self):
        with self.assertRaises(ValueError):
            self.engine.add_fact("[invalid regex(", "Some fact")

    def test_match_facts(self):
        self.engine.add_fact("bruno|api", "Use --condense flag for Bruno", fact_id="bruno-1")
        self.engine.add_fact("docker", "Use docker compose v2", fact_id="docker-1")

        # Test prompt matching bruno
        matched = self.engine.match("Can you run the bruno collection?")
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["id"], "bruno-1")
        self.assertEqual(matched[0]["hits"], 1)

        # Test prompt matching both
        matched_both = self.engine.match("Run bruno api inside docker container")
        self.assertEqual(len(matched_both), 2)

        # Test prompt matching none
        matched_none = self.engine.match("Write a python function")
        self.assertEqual(len(matched_none), 0)

    def test_format_ephemeral_message(self):
        self.engine.add_fact("bruno", "Fact A", fact_id="a")
        matched = self.engine.match("bruno")
        msg = self.engine.format_ephemeral_message(matched)
        self.assertIsNotNone(msg)
        self.assertIn("[CENTSIBLE MICRO-SKILLS: Active Repository Facts]", msg)
        self.assertIn("- Fact A", msg)

        # Empty match returns None
        self.assertIsNone(self.engine.format_ephemeral_message([]))

    def test_remove_fact(self):
        self.engine.add_fact("test-pattern", "Fact to remove", fact_id="rem-1")
        self.assertTrue(self.engine.remove_fact("rem-1"))
        self.assertEqual(len(self.engine.list_facts()), 0)

        # Non-existing returns False
        self.assertFalse(self.engine.remove_fact("non-existing"))

    def test_match_tool_call_missing_vs_present_flags(self):
        self.engine.add_fact(
            "bruno",
            "Append --condense to Bruno commands on UAT",
            fact_id="bruno-condense",
        )
        # Missing --condense -> triggers tool hint
        missing_call = {"name": "run_command", "args": {"CommandLine": "bruno run uat"}}
        matched = self.engine.match_tool_call(missing_call)
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["id"], "bruno-condense")

        # Already has --condense -> no redundant hint
        valid_call = {"name": "run_command", "args": {"CommandLine": "bruno run uat --condense"}}
        matched_valid = self.engine.match_tool_call(valid_call)
        self.assertEqual(len(matched_valid), 0)

        # Already injected in prompt (exclude_ids) -> skipped
        matched_excluded = self.engine.match_tool_call(missing_call, exclude_ids=["bruno-condense"])
        self.assertEqual(len(matched_excluded), 0)

    def test_auto_learn_from_recovery_and_metrics_roi(self):
        learned = self.engine.auto_learn_from_recovery("pytest tests/", "python3 -m pytest tests/ -q")
        self.assertIsNotNone(learned)
        self.assertEqual(learned["category"], "auto-learned")
        self.assertIn("python3 -m pytest tests/ -q", learned["fact"])

        # Trigger a hit and verify ROI summary
        self.engine.match("Please run pytest now")
        summary = self.engine.get_metrics_summary(usd_per_mtok=3.0)
        self.assertEqual(summary["total_facts"], 1)
        self.assertEqual(summary["auto_learned_facts"], 1)
        self.assertEqual(summary["fact_hits"], 1)
        self.assertGreater(summary["net_saved_tokens"], 1400)
        self.assertGreater(summary["saved_cents"], 0.4)
        self.assertGreater(summary["roi_multiplier"], 10.0)

    def test_auto_learn_handles_wrappers_and_compound_commands(self):
        cases = [
            ("cd /x && pytest tests/", "cd /x && python3 -m pytest tests/ -q", "pytest"),
            ("rtk pytest tests", "rtk proxy pytest tests -q", "pytest"),
            ("npm test", "npm run test:ci", "npm"),
        ]
        for failed, ok, expected in cases:
            learned = self.engine.auto_learn_from_recovery(failed, ok)
            self.assertIsNotNone(learned, failed)
            self.assertEqual(learned["pattern"], expected)
        # Different tools or no known tool -> nothing learned
        self.assertIsNone(self.engine.auto_learn_from_recovery("pytest x", "npm test"))
        self.assertIsNone(self.engine.auto_learn_from_recovery("./foo a", "./foo b"))

    def test_vietnamese_nfc_nfd_and_accent_insensitive_matching(self):
        import unicodedata

        self.engine.add_fact(
            "kiểm thử|chạy đơ",
            "Dùng 'pytest -q' trên môi trường 'thử nghiệm'",
            fact_id="vn-test",
        )
        # 1. Exact NFC Vietnamese match
        self.assertEqual(len(self.engine.match("Hãy chạy kiểm thử giúp tôi")), 1)
        # 2. macOS NFD decomposed Unicode match
        nfd_prompt = unicodedata.normalize("NFD", "Hãy chạy kiểm thử giúp tôi")
        self.assertEqual(len(self.engine.match(nfd_prompt)), 1)
        # 3. Non-diacritic (không dấu, gồm cả đ -> d) fallback match
        self.assertEqual(len(self.engine.match("Hay chay kiem thu va chay do giup toi")), 1)

    def test_get_default_facts_path_resolution(self):
        with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as global_home:
            ws_path = Path(ws_dir).resolve()
            old_env = os.environ.get("CENTSIBLE_HOME")
            old_cwd = Path.cwd()
            try:
                os.environ["CENTSIBLE_HOME"] = global_home
                os.chdir(ws_path)

                # 1. Neither .agents nor .claude exists -> neutral global path
                resolved_global = get_default_facts_path([str(ws_path)])
                self.assertEqual(resolved_global, Path(global_home).resolve() / "facts.jsonl")

                # 2. Only .claude exists -> project .claude/facts.jsonl
                claude_dir = ws_path / ".claude"
                claude_dir.mkdir()
                resolved_claude = get_default_facts_path([str(ws_path)])
                self.assertEqual(resolved_claude, claude_dir / "facts.jsonl")

                # 3. Both .agents and .claude exist -> .agents/facts.jsonl takes precedence
                agents_dir = ws_path / ".agents"
                agents_dir.mkdir()
                resolved_agents = get_default_facts_path([str(ws_path)])
                self.assertEqual(resolved_agents, agents_dir / "facts.jsonl")
            finally:
                os.chdir(old_cwd)
                if old_env is None:
                    os.environ.pop("CENTSIBLE_HOME", None)
                else:
                    os.environ["CENTSIBLE_HOME"] = old_env


if __name__ == "__main__":
    unittest.main()
