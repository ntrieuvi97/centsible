import pytest


@pytest.fixture(autouse=True)
def _isolated_event_log(tmp_path, monkeypatch):
    """Tests must never append to the user's real ~/.centsible/events.jsonl."""
    monkeypatch.setattr("core.event_log.get_event_log_path", lambda: tmp_path / "events.jsonl")
