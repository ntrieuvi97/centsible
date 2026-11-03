#!/usr/bin/env python3
"""
Unit tests for FactsEngine.
"""

import tempfile
import unittest
from pathlib import Path

from core.facts_engine import FactsEngine


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
        self.assertIn("[TOKEN-ECONOMIZER MICRO-SKILLS: Active Repository Facts]", msg)
        self.assertIn("- Fact A", msg)

        # Empty match returns None
        self.assertIsNone(self.engine.format_ephemeral_message([]))

    def test_remove_fact(self):
        self.engine.add_fact("test-pattern", "Fact to remove", fact_id="rem-1")
        self.assertTrue(self.engine.remove_fact("rem-1"))
        self.assertEqual(len(self.engine.list_facts()), 0)

        # Non-existing returns False
        self.assertFalse(self.engine.remove_fact("non-existing"))


if __name__ == "__main__":
    unittest.main()
