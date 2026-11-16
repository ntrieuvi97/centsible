"""Tool-level recovery learning: any tool (not only shell) that fails and is then fixed by one clean change."""

import json

import pytest

from core.cli_parser import build_parser
from core.commands import cmd_facts
from core.facts_engine import FactsEngine, tool_call_signature
from core.loop_guard import LoopGuard, extract_error_from_payload, load_config, save_config

EXEC = "mcp__openclaw__exec"
CLOUD_A = "11111111-aaaa-4bbb-8ccc-000000000001"
CLOUD_B = "22222222-aaaa-4bbb-8ccc-000000000002"
NOT_GRANTED = f"Cloud id: {CLOUD_A} isn't explicitly granted by the user."
TIMEOUT_ERR = 'exec parameter "timeout" is unsupported; use "timeoutSeconds" instead'


@pytest.fixture
def engine(tmp_path):
    return FactsEngine(tmp_path / "facts.jsonl")


@pytest.fixture
def guard():
    g = LoopGuard("tool_recovery", max_identical_calls=9, max_consecutive_errors=9)
    yield g
    g.reset()


def _fail_then_ok(guard, engine, tool, bad_args, good_args, error):
    guard.record_post_tool_use({"name": tool, "args": bad_args}, error=error, engine=engine)
    guard.record_post_tool_use({"name": tool, "args": good_args}, error=None, engine=engine)


def _sig(tool, **args):
    return tool_call_signature(tool, args)


# (a) tool-name-only change: no hook fires for a nonexistent tool, so it is never learned
def test_tool_name_change_learns_nothing(engine):
    failed, ok = _sig("exec", command="ls"), _sig(EXEC, command="ls")
    assert engine.auto_learn_from_tool_recovery(failed, ok, "No such tool available: exec") is None
    assert engine.list_facts() == []


# (b) one arg KEY renamed, error names both
def test_arg_key_rename_learned_end_to_end(guard, engine):
    _fail_then_ok(guard, engine, EXEC, {"command": "npm run build", "timeout": 30},
                  {"command": "npm run build", "timeoutSeconds": 30}, TIMEOUT_ERR)
    (fact,) = engine.list_facts()
    assert fact["id"].startswith("auto_tool_") and fact["category"] == "auto-learned"
    assert fact["tool"] == f"^{EXEC}$" and fact["arg_keys"] == ["timeout"] and fact["pattern"] == ""
    assert '"timeoutSeconds"' in fact["fact"] and "\n" not in fact["fact"] and len(fact["fact"]) <= 240
    assert engine.load_metrics()["auto_learned_facts"] == 1
    # re-learning the same pair updates in place and is not counted twice
    _fail_then_ok(guard, engine, EXEC, {"command": "ls", "timeout": 5}, {"command": "ls", "timeoutSeconds": 5}, TIMEOUT_ERR)
    assert len(engine.list_facts()) == 1 and engine.load_metrics()["auto_learned_facts"] == 1


def test_arg_key_rename_needs_error_naming_both_keys(engine):
    failed, ok = _sig(EXEC, command="ls", timeout=5), _sig(EXEC, command="ls", timeoutSeconds=5)
    assert engine.auto_learn_from_tool_recovery(failed, ok, "bad request") is None
    assert engine.auto_learn_from_tool_recovery(failed, ok, 'use "timeoutSeconds" instead') is None
    # two keys swapped at once is not a single-variable change
    two = _sig(EXEC, command="ls", wait=1, timeoutSeconds=5)
    assert engine.auto_learn_from_tool_recovery(failed, two, TIMEOUT_ERR) is None


# (c) one id-like value changed, only with a denied/not-granted style error naming the failed value
def test_cloud_id_change_learned_with_not_granted_error(guard, engine):
    _fail_then_ok(guard, engine, "mcp__atlassian__getJiraIssue", {"cloudId": CLOUD_A, "issueIdOrKey": "AP-1"},
                  {"cloudId": CLOUD_B, "issueIdOrKey": "AP-1"}, NOT_GRANTED)
    (fact,) = engine.list_facts()
    assert fact["arg_keys"] == ["cloudId"] and CLOUD_B in fact["fact"] and CLOUD_A in fact["fact"]
    assert fact["fact"].index(CLOUD_B) < fact["fact"].index("instead")  # the new value is the prescribed one


def test_value_change_not_learned_without_marker_or_failed_value_in_error(engine):
    failed, ok = _sig("t_search", cloudId=CLOUD_A), _sig("t_search", cloudId=CLOUD_B)
    assert engine.auto_learn_from_tool_recovery(failed, ok, "request timed out") is None
    assert engine.auto_learn_from_tool_recovery(failed, ok, "Cloud id: other isn't explicitly granted") is None
    # short ids are lookups (issue keys), never recoveries
    k1, k2 = _sig("mcp__jira__getIssue", issueKey="AP-1"), _sig("mcp__jira__getIssue", issueKey="AP-2")
    assert engine.auto_learn_from_tool_recovery(k1, k2, "AP-1 not found") is None
    assert engine.list_facts() == []


def test_custom_markers_are_honoured(engine):
    failed, ok = _sig("mcp__acme__fetch_things", cloudId=CLOUD_A), _sig("mcp__acme__fetch_things", cloudId=CLOUD_B)
    err = f"{CLOUD_A} is gone"
    assert engine.auto_learn_from_tool_recovery(failed, ok, err) is None
    assert engine.auto_learn_from_tool_recovery(failed, ok, err, markers=["is gone"])


def test_markers_come_from_config(guard, engine):
    save_config({**load_config(), "tool_error_markers": ["Is Gone"]})
    _fail_then_ok(guard, engine, "mcp__acme__fetch_things", {"cloudId": CLOUD_A}, {"cloudId": CLOUD_B}, NOT_GRANTED)
    assert engine.list_facts() == []  # default markers replaced
    _fail_then_ok(guard, engine, "mcp__acme__fetch_things", {"cloudId": CLOUD_A}, {"cloudId": CLOUD_B}, f"{CLOUD_A} is gone")
    assert len(engine.list_facts()) == 1


# (d) identical retry
def test_identical_call_retried_learns_nothing(guard, engine):
    args = {"cloudId": CLOUD_A, "query": "x"}
    _fail_then_ok(guard, engine, "mcp__atlassian__search", args, dict(args), NOT_GRANTED)
    assert engine.list_facts() == []


# (e) secrets
def test_secret_looking_value_never_stored(guard, engine, tmp_path):
    secret_a, secret_b = "sk-AAAAAAAAAAAAAAAAAAAA", "sk-BBBBBBBBBBBBBBBBBBBB"
    for key in ("apiKey", "someId"):  # secret-named key, and a value with a known token shape
        _fail_then_ok(guard, engine, "mcp__acme__fetch_things", {key: secret_a}, {key: secret_b},
                      f"{secret_a} denied")
    assert engine.list_facts() == []
    state = guard.state_path.read_text(encoding="utf-8")
    assert secret_a not in state and secret_b not in state


def test_signature_drops_free_text_and_redacts_error(guard, engine):
    sig = tool_call_signature(EXEC, {"command": "echo hi", "cloudId": CLOUD_A, "note": "not an id!", "n": 3})
    assert sig == {"tool": EXEC, "keys": ["cloudId", "command", "n", "note"], "vals": {"cloudId": CLOUD_A}}
    guard.record_post_tool_use({"name": EXEC, "args": {"command": "x"}}, error="failed --token=abc123secret", engine=engine)
    stored = json.loads(guard.state_path.read_text(encoding="utf-8"))["last_failed_call"]
    assert "abc123secret" not in json.dumps(stored)


def test_min_tool_recovery_failures_is_configurable(guard, engine, monkeypatch):
    monkeypatch.setenv("CENTSIBLE_MIN_TOOL_RECOVERY_FAILURES", "2")
    _fail_then_ok(guard, engine, EXEC, {"command": "ls", "timeout": 5}, {"command": "ls", "timeoutSeconds": 5}, TIMEOUT_ERR)
    assert engine.list_facts() == []
    guard.record_post_tool_use({"name": EXEC, "args": {"command": "ls", "timeout": 5}}, error=TIMEOUT_ERR, engine=engine)
    guard.record_post_tool_use({"name": EXEC, "args": {"command": "ls", "timeout": 5}}, error=TIMEOUT_ERR, engine=engine)
    guard.record_post_tool_use({"name": EXEC, "args": {"command": "ls", "timeoutSeconds": 5}}, error=None, engine=engine)
    assert len(engine.list_facts()) == 1


def test_stale_failure_is_not_learned(guard, engine):
    guard.record_post_tool_use({"name": EXEC, "args": {"command": "ls", "timeout": 5}}, error=TIMEOUT_ERR, engine=engine)
    for i in range(12):
        guard.record_post_tool_use({"name": "Glob", "args": {"pattern": f"*{i}"}}, error=None, engine=engine)
    guard.record_post_tool_use({"name": EXEC, "args": {"command": "ls", "timeoutSeconds": 5}}, error=None, engine=engine)
    assert engine.list_facts() == []


def test_manual_fact_is_never_overwritten(engine):
    engine.add_fact("", "Hand-written rule: always pass timeoutSeconds.", category="gotcha", tool=f"^{EXEC}$", arg_keys=["timeout"])
    failed, ok = _sig(EXEC, command="ls", timeout=5), _sig(EXEC, command="ls", timeoutSeconds=5)
    assert engine.auto_learn_from_tool_recovery(failed, ok, TIMEOUT_ERR) is None
    (fact,) = engine.list_facts()
    assert fact["category"] == "gotcha"


def test_bare_generic_tool_name_rejected(engine):
    failed, ok = _sig("exec", command="ls", timeout=5), _sig("exec", command="ls", timeoutSeconds=5)
    assert engine.auto_learn_from_tool_recovery(failed, ok, TIMEOUT_ERR) is None


# injection reachable
def test_tool_fact_fires_on_matching_call_only(engine):
    engine.add_fact("", 'Pass "timeoutSeconds" instead of "timeout".', category="gotcha", tool=f"^{EXEC}$", arg_keys=["timeout"])
    hit = engine.match_tool_call({"name": EXEC, "args": {"command": "ls", "timeout": 5}})
    assert [f["fact"] for f in hit] == ['Pass "timeoutSeconds" instead of "timeout".']
    assert engine.list_facts()[0]["hits"] == 1
    assert engine.match_tool_call({"name": EXEC, "args": {"command": "ls", "timeoutSeconds": 5}}) == []
    assert engine.match_tool_call({"name": "mcp__openclaw__read", "args": {"timeout": 5}}) == []
    assert engine.match_tool_call({"name": EXEC, "args": {"timeout": 5}}, exclude_ids=[hit[0]["id"]]) == []


def test_tool_fact_without_arg_keys_fires_with_no_command_or_path(engine):
    engine.add_fact("", "Gateway tool needs the site header set first.", category="gotcha", tool="^mcp__gateway__.*$")
    assert engine.match_tool_call({"name": "mcp__gateway__call", "args": {}})
    assert engine.match("mcp__gateway__call please", record_hits=False) == []  # never fires on prompts


def test_pre_tool_hook_injects_tool_fact(guard, engine):
    engine.add_fact("", 'Pass "timeoutSeconds" instead of "timeout".', category="gotcha", tool=f"^{EXEC}$", arg_keys=["timeout"])
    decision, reason = guard.check_pre_tool_use({"name": EXEC, "args": {"command": "ls", "timeout": 5}}, engine=engine)
    assert decision == "allow" and "timeoutSeconds" in reason


# (f) facts without `tool` unchanged
def test_facts_without_tool_behave_as_before(engine):
    engine.add_fact(r"deploy_staging", "Use --dry-run first.", category="gotcha")
    assert [f["id"] for f in engine.match_tool_call({"name": "Bash", "args": {"command": "./deploy_staging now"}})]
    assert engine.match_tool_call({"name": "Bash", "args": {}}) == []
    assert engine.match_tool_call({"name": "mcp__x__y", "args": {"foo": "bar"}}) == []
    assert "tool" not in engine.list_facts()[0] and "arg_keys" not in engine.list_facts()[0]
    with pytest.raises(ValueError):
        engine.add_fact("", "no trigger at all")


# (g) shell recovery regression
def test_shell_recovery_path_unchanged(guard, engine):
    guard.record_post_tool_use({"name": "Bash", "args": {"command": "pytest tests/ --bad-flag"}}, error="exit code 2", engine=engine)
    guard.record_post_tool_use({"name": "Bash", "args": {"command": "pytest tests/ --bad-flag"}}, error="exit code 2", engine=engine)
    guard.record_post_tool_use({"name": "Bash", "args": {"command": "python3 -m pytest tests/ -q"}}, error=None, engine=engine)
    facts = engine.list_facts()
    assert len(facts) == 1 and facts[0]["id"].startswith("auto_") and not facts[0]["id"].startswith("auto_tool_")
    assert "tool" not in facts[0]


def test_single_shell_failure_still_not_learned(guard, engine):
    guard.record_post_tool_use({"name": "Bash", "args": {"command": "pytest tests/ --bad-flag"}}, error="exit code 2", engine=engine)
    guard.record_post_tool_use({"name": "Bash", "args": {"command": "python3 -m pytest tests/ -q"}}, error=None, engine=engine)
    assert engine.list_facts() == []


# soft MCP errors reach the tracker
def test_soft_mcp_error_detected_in_content_blocks():
    soft = [{"type": "text", "text": json.dumps({"error": True, "message": NOT_GRANTED})}]
    assert extract_error_from_payload({"tool_response": soft}) == NOT_GRANTED
    ok = [{"type": "text", "text": json.dumps({"error": False})}]
    assert extract_error_from_payload({"tool_response": ok}) is None
    assert extract_error_from_payload({"tool_response": [{"type": "text", "text": "plain {not json"}]}) is None
    assert extract_error_from_payload({"tool_response": {"stdout": '{"error": true}'}}) is None  # shell output is not an MCP error


# CLI
def test_cli_facts_add_tool_only_and_list(engine, capsys):
    parser, _, facts_parser = build_parser()
    args = parser.parse_args(["facts", "add", "--tool", f"^{EXEC}$", "--fact", 'Pass "timeoutSeconds" not "timeout".', "-c", "gotcha"])
    assert cmd_facts(args, engine, facts_parser) == 0
    assert cmd_facts(parser.parse_args(["facts", "list"]), engine, facts_parser) == 0
    out = capsys.readouterr().out
    assert f"Tool:    ^{EXEC}$" in out and "Pattern:" not in out
    # neither pattern nor tool, and a bare generic tool name, are rejected
    assert cmd_facts(parser.parse_args(["facts", "add", "--fact", "x y z"]), engine, facts_parser) == 2
    assert cmd_facts(parser.parse_args(["facts", "add", "--tool", "exec", "--fact", "Pass timeoutSeconds."]), engine, facts_parser) == 2
    assert len(engine.list_facts()) == 1
