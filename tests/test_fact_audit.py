import json
import tempfile
import unittest
from pathlib import Path

from core.fact_audit import replay_facts, verify_facts


class TestVerify(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "app.py").write_text("x")
        (self.root / "Makefile").write_text("")

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, fact, pattern="x_y_z"):
        return verify_facts([{"id": "f", "pattern": pattern, "fact": fact}], self.root)

    def test_existing_and_missing_paths(self):
        self.assertEqual(self.check("Edit src/app.py only."), [])
        self.assertEqual(self.check("Edit app.py only."), [])  # bare name found anywhere
        msgs = [i["message"] for i in self.check("Never touch src/gone.py or old.py.")]
        self.assertEqual(len(msgs), 2)
        self.assertTrue(all(i["level"] == "stale" for i in self.check("See src/gone.py.")))

    def test_prose_slashes_and_urls_are_ignored(self):
        self.assertEqual(self.check("Use client/server split, and/or retry; docs at https://x.io/a/b.md"), [])

    def test_missing_binary_is_only_a_warning(self):
        issues = self.check("Run 'nonexistent_tool_xyz --fast' first.")
        self.assertEqual([i["level"] for i in issues], ["warn"])


class TestReplay(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        rows = []
        for i in range(25):
            rows.append({"type": "user", "message": {"content": f"please look at thing {i}"}})
        rows.append({"type": "user", "message": {"content": "run deploy_staging now"}})
        for _ in range(6):
            rows.append({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Bash", "input": {"command": "pytest -q tests"}}]}})
        rows.append({"type": "user", "isSidechain": True, "message": {"content": "deploy_staging"}})
        (d / "s1.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        self.dir = d

    def tearDown(self):
        self.tmp.cleanup()

    def run_one(self, pattern, fact="x"):
        res = replay_facts([{"id": "f", "pattern": pattern, "fact": fact}], self.dir)
        return res, res["facts"][0]

    def test_counts_and_sidechain_skipped(self):
        res, r = self.run_one("deploy_staging")
        self.assertEqual((r["prompt_fires"], res["totals"]["prompts"]), (1, 26))
        self.assertEqual(r["verdict"], "ok")

    def test_never_fires_and_too_broad(self):
        self.assertTrue(self.run_one("kubectl_apply")[1]["verdict"].startswith("never fires"))
        self.assertTrue(self.run_one("please|thing")[1]["verdict"].startswith("too broad"))

    def test_redundant_when_commands_already_comply(self):
        r = self.run_one("pytest", "Run 'pytest -q' always.")[1]
        self.assertTrue(r["verdict"].startswith("redundant"))


if __name__ == "__main__":
    unittest.main()
