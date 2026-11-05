import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

from core.event_log import agent_from_conversation, log_event, read_events, summarize_events
from core.facts_engine import FactsEngine
from core.loop_guard import LoopGuard

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CENTSIBLE_HOME", str(tmp_path))
    monkeypatch.undo()  # drop conftest's path patch so the real resolver honours CENTSIBLE_HOME
    monkeypatch.setenv("CENTSIBLE_HOME", str(tmp_path))
    return tmp_path


def test_agent_parsed_from_openclaw_session_key():
    assert agent_from_conversation("agent:main:telegram:direct:1") == "main"
    assert agent_from_conversation("abc") == ""


def test_log_event_drops_empty_fields_and_survives_bad_home(home, monkeypatch):
    log_event("hint", "agent:troubleshoot:x", tool="exec", fact_ids=[])
    rec = read_events()[0]
    assert rec["agent"] == "troubleshoot" and "fact_ids" not in rec
    monkeypatch.setenv("CENTSIBLE_HOME", "/dev/null/nope")
    log_event("hint", "c")  # must not raise


def test_hint_and_applied_are_logged(home, tmp_path):
    engine = FactsEngine(storage_path=tmp_path / "facts.jsonl", global_path=tmp_path / "g.jsonl")
    fact = engine.add_fact(pattern="pytest.{0,30}(tests)", fact="run pytest -q", category="gotcha")
    cid = f"agent:main:{uuid.uuid4().hex}"
    guard = LoopGuard(cid)
    call = {"name": "exec", "args": {"command": "pytest tests"}}
    guard.check_pre_tool_use(call, engine=engine)
    guard.record_post_tool_use({"name": "exec", "args": {"command": "pytest -q tests"}}, None, engine=engine)
    kinds = [e["event"] for e in read_events() if e["session"] == cid]
    assert "hint" in kinds
    summ = summarize_events(read_events())
    assert summ["agents"]["main"]["hint"] >= 1
    assert fact["id"] in summ["facts"]


def test_cli_events_json(home):
    log_event("prompt_inject", "agent:main:s", fact_ids=["f1"])
    out = subprocess.run(
        [sys.executable, "-m", "core.cli", "events", "--json"],
        cwd=ROOT, capture_output=True, text=True, env={**__import__("os").environ, "CENTSIBLE_HOME": str(home)},
    )
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["facts"]["f1"]["shown"] == 1
