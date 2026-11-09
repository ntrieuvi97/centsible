import pytest


@pytest.fixture(autouse=True)
def _isolated_event_log(tmp_path, monkeypatch):
    """Tests must never append to the user's real ~/.centsible/events.jsonl."""
    monkeypatch.setenv("CENTSIBLE_HOME", str(tmp_path))
