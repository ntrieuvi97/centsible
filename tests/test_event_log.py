import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from core.event_log import agent_from_conversation, log_event, read_events, summarize_events
from core.facts_engine import FactsEngine
from core.loop_guard import LoopGuard

ROOT = Path(__file__).resolve().parent.parent


class TestEventLog(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.home = Path(self.temp_dir.name)
        self.old_home = os.environ.get("CENTSIBLE_HOME")
        os.environ["CENTSIBLE_HOME"] = str(self.home)

    def tearDown(self):
        if self.old_home is not None:
            os.environ["CENTSIBLE_HOME"] = self.old_home
        else:
            os.environ.pop("CENTSIBLE_HOME", None)
        self.temp_dir.cleanup()

    def test_agent_parsed_from_openclaw_session_key(self):
        self.assertEqual(agent_from_conversation("agent:main:telegram:direct:1"), "main")
        self.assertEqual(agent_from_conversation("abc"), "")

    def test_log_event_drops_empty_fields_and_survives_bad_home(self):
        log_event("hint", "agent:troubleshoot:x", tool="exec", fact_ids=[])
        rec = read_events()[0]
        self.assertEqual(rec["agent"], "troubleshoot")
        self.assertNotIn("fact_ids", rec)
        os.environ["CENTSIBLE_HOME"] = "/dev/null/nope"
        log_event("hint", "c")  # must not raise

    def test_hint_and_applied_are_logged(self):
        engine = FactsEngine(storage_path=self.home / "facts.jsonl", global_path=self.home / "g.jsonl")
        fact = engine.add_fact(pattern=r"pytest.{0,30}(tests)", fact="run pytest -q", category="gotcha")
        cid = f"agent:main:{uuid.uuid4().hex}"
        guard = LoopGuard(cid)
        call = {"name": "exec", "args": {"command": "pytest tests"}}
        guard.check_pre_tool_use(call, engine=engine)
        guard.record_post_tool_use({"name": "exec", "args": {"command": "pytest -q tests"}}, None, engine=engine)
        kinds = [e["event"] for e in read_events() if e["session"] == cid]
        self.assertIn("hint", kinds)
        summ = summarize_events(read_events())
        self.assertGreaterEqual(summ["agents"]["main"]["hint"], 1)
        self.assertIn(fact["id"], summ["facts"])

    def test_cli_events_json(self):
        log_event("prompt_inject", "agent:main:s", fact_ids=["f1"])
        env = {**os.environ, "CENTSIBLE_HOME": str(self.home)}
        out = subprocess.run(
            [sys.executable, "-m", "core.cli", "events", "--json"],
            cwd=ROOT, capture_output=True, text=True, env=env,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout)["facts"]["f1"]["shown"], 1)


if __name__ == "__main__":
    unittest.main()
