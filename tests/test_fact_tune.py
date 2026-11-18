"""`facts tune`: remove / narrow / merge suggestions and --apply."""

import json
import tempfile
import unittest
from pathlib import Path

from core.facts_engine import FactsEngine
from core.fact_tune import apply_suggestions, split_alternation, tune_facts


def _write_sessions(d: Path, sessions):
    for name, prompts in sessions.items():
        rows = [{"type": "user", "message": {"content": p}} for p in prompts]
        (d / f"{name}.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")


class TestSplit(unittest.TestCase):
    def test_top_level_only(self):
        self.assertEqual(split_alternation("a|b(c|d)|[x|y]|e\\|f"), ["a", "b(c|d)", "[x|y]", "e\\|f"])
        self.assertEqual(split_alternation("single"), ["single"])


class TestTune(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        filler = [f"look at thing {i}" for i in range(20)]
        _write_sessions(self.dir, {
            "s1": filler + ["please deploy_staging the api"],
            "s2": ["foo_bar_baz broke again"],
            "s3": ["check foo_bar_baz output"],
            "s4": ["foo_bar_baz once more"],
        })
        self.applied = [{"event": "applied", "session": "s1", "fact_ids": ["noisy"]}]

    def tearDown(self):
        self.tmp.cleanup()

    def by_id(self, result):
        return {s["id"]: s for s in result["suggestions"]}

    def test_narrow_drops_branch_never_applied(self):
        facts = [{"id": "noisy", "pattern": "deploy_staging|foo_bar_baz", "fact": "x", "hits": 30, "attributed_hits": 1}]
        s = self.by_id(tune_facts(facts, self.dir, None, self.applied))["noisy"]
        self.assertEqual((s["action"], s["applicable"], s["new_pattern"]), ("narrow", True, "deploy_staging"))
        self.assertEqual((s["fires_before"], s["fires_after"]), (4, 1))

    def test_noisy_without_evidence_is_manual(self):
        facts = [{"id": "noisy", "pattern": "deploy_staging|foo_bar_baz", "fact": "x", "hits": 30, "attributed_hits": 1}]
        s = self.by_id(tune_facts(facts, None, None, []))["noisy"]
        self.assertEqual((s["action"], s["applicable"]), ("narrow", False))
        self.assertIn("--transcripts", s["reason"])

    def test_remove_dead_and_merge_duplicate_text(self):
        facts = [
            {"id": "dead", "pattern": "kubectl_apply_prod", "fact": "Use the wrapper.", "hits": 0},
            {"id": "a", "pattern": "deploy_staging", "fact": "Run make deploy.", "hits": 5, "attributed_hits": 3},
            {"id": "b", "pattern": "deploy_staging_v2", "fact": "run make deploy.", "hits": 5, "attributed_hits": 0},
            {"id": "keep", "pattern": "*", "fact": "always on", "category": "always"},
        ]
        s = self.by_id(tune_facts(facts, self.dir, None, []))
        self.assertEqual((s["dead"]["action"], s["dead"]["applicable"]), ("remove", True))
        self.assertEqual((s["b"]["action"], s["b"]["keep_id"]), ("merge", "a"))
        self.assertNotIn("keep", s)
        self.assertNotIn("a", s)


class TestApply(unittest.TestCase):
    def test_apply_narrows_in_place_and_removes(self):
        with tempfile.TemporaryDirectory() as d:
            eng = FactsEngine(Path(d) / "facts.jsonl")
            eng.add_fact("deploy_staging|foo_bar_baz", "Run 'make deploy_staging'.", fact_id="n", category="workflow")
            eng.add_fact("kubectl_apply_prod", "Use 'kubectl-safe apply'.", fact_id="dead", category="workflow")
            done = apply_suggestions(eng, [
                {"action": "narrow", "id": "n", "pattern": "deploy_staging|foo_bar_baz", "new_pattern": "deploy_staging",
                 "applicable": True, "scope": "project"},
                {"action": "remove", "id": "dead", "applicable": True, "scope": "project"},
                {"action": "merge", "id": "n", "applicable": False, "scope": "project"},
            ])
            self.assertEqual(len(done), 2)
            self.assertEqual(eng.get_fact("n")["pattern"], "deploy_staging")
            self.assertEqual(eng.get_fact("n")["category"], "workflow")
            self.assertIsNone(eng.get_fact("dead"))


if __name__ == "__main__":
    unittest.main()
