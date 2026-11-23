#!/usr/bin/env python3
"""Tests for the Centsible MCP Apps stdio server and status --json."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestMcpServer(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.project_dir = Path(self.temp_dir.name) / "demo-repo"
        (self.project_dir / ".agents").mkdir(parents=True)
        (self.project_dir / ".agents" / "facts.jsonl").write_text(
            json.dumps({"id": "f1", "pattern": "pytest", "fact": "Use pytest -q"}) + "\n",
            encoding="utf-8",
        )
        (self.project_dir / ".agents" / "facts.stats.json").write_text(
            json.dumps({"f1": {"hits": 3, "attributed_hits": 2}}),
            encoding="utf-8",
        )
        self.centsible_home = Path(self.temp_dir.name) / "home"
        self.centsible_home.mkdir(parents=True)
        (self.centsible_home / "events.jsonl").write_text(
            json.dumps({
                "ts": "2026-11-23T01:00:00Z",
                "event": "applied",
                "session": "s1",
                "agent": "claude",
                "project": "demo-repo",
                "fact_ids": ["f1"],
            }) + "\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def _run_mcp(self, requests):
        stdin_payload = "".join(json.dumps(r) + "\n" for r in requests)
        env = dict(os.environ, CENTSIBLE_HOME=str(self.centsible_home))
        res = subprocess.run(
            [sys.executable, str(REPO_ROOT / "core" / "cli.py"), "mcp"],
            input=stdin_payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(self.project_dir),
            env=env,
            timeout=15,
        )
        self.assertEqual(res.returncode, 0, msg=res.stderr)
        lines = [line for line in res.stdout.splitlines() if line.strip()]
        return [json.loads(line) for line in lines]

    def test_mcp_initialize_tools_resources_and_call(self):
        responses = self._run_mcp([
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "ping"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 4, "method": "resources/list"},
            {"jsonrpc": "2.0", "id": 5, "method": "resources/read", "params": {"uri": "ui://centsible/dashboard.html"}},
            {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "open_dashboard", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "get_dashboard_data", "arguments": {"scope": "project"}}},
        ])
        # Notification produces no response -> 7 responses total
        self.assertEqual([r["id"] for r in responses], [1, 2, 3, 4, 5, 6, 7])

        init_res = responses[0]["result"]
        self.assertEqual(init_res["serverInfo"]["name"], "centsible")
        self.assertIn("tools", init_res["capabilities"])
        self.assertIn("resources", init_res["capabilities"])

        tools = {t["name"]: t for t in responses[2]["result"]["tools"]}
        self.assertEqual(tools["open_dashboard"]["_meta"]["ui"]["resourceUri"], "ui://centsible/dashboard.html")
        self.assertIn("com.google.antigravity/ui", tools["open_dashboard"]["_meta"])
        self.assertEqual(tools["get_dashboard_data"]["_meta"]["ui"]["visibility"], ["app"])

        res_list = responses[3]["result"]["resources"]
        self.assertEqual(res_list[0]["uri"], "ui://centsible/dashboard.html")
        self.assertEqual(res_list[0]["mimeType"], "text/html;profile=mcp-app")

        res_read = responses[4]["result"]["contents"][0]
        self.assertEqual(res_read["mimeType"], "text/html;profile=mcp-app")
        self.assertIn("Centsible Live Status", res_read["text"])
        self.assertEqual(res_read["text"].count("<!DOCTYPE html>"), 1)
        self.assertEqual(res_read["text"].lower().count("</script>"), 1)
        self.assertLess(len(res_read["text"].encode("utf-8")), 10 * 1024 * 1024)

        for idx in (5, 6):
            call_res = responses[idx]["result"]
            self.assertFalse(call_res["isError"])
            sc = call_res["structuredContent"]
            self.assertEqual(sc["scope"], "project")
            self.assertEqual(sc["metrics"]["total_facts"], 1)
            self.assertEqual(sc["metrics"]["fact_hits"], 3)
            self.assertEqual(sc["metrics"]["attributed_hits"], 2)
            self.assertTrue(sc["status"]["active"])
            self.assertEqual(sc["events"]["total"], 1)
            self.assertEqual(len(sc["events"]["recent"]), 1)

    def test_status_json_and_plugin_manifests(self):
        env = dict(os.environ, CENTSIBLE_HOME=str(self.centsible_home))
        res = subprocess.run(
            [sys.executable, str(REPO_ROOT / "core" / "cli.py"), "status", "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            cwd=str(self.project_dir),
            env=env,
            timeout=10,
        )
        self.assertEqual(res.returncode, 0, msg=res.stderr)
        data = json.loads(res.stdout)
        self.assertTrue(data["active"])
        self.assertEqual(data["metrics"]["total_facts"], 1)
        self.assertIn("mode", data["loop_guard"])

        antigravity_mcp = json.loads((REPO_ROOT / "mcp_config.json").read_text(encoding="utf-8"))
        claude_mcp = json.loads((REPO_ROOT / ".mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(antigravity_mcp["mcpServers"]["centsible"]["command"], "python3")
        self.assertEqual(antigravity_mcp["mcpServers"]["centsible"]["args"], ["${PLUGIN_ROOT}/bin/centsible", "mcp"])
        self.assertEqual(claude_mcp["mcpServers"]["centsible"]["command"], "${CLAUDE_PLUGIN_ROOT}/bin/centsible")
        self.assertEqual(claude_mcp["mcpServers"]["centsible"]["args"], ["mcp"])

    def test_auto_fallback_to_all_when_cwd_has_no_data(self):
        # Register demo-repo in CENTSIBLE_HOME, then launch from an empty directory without scope arg
        env = dict(os.environ, CENTSIBLE_HOME=str(self.centsible_home))
        subprocess.run(
            [sys.executable, str(REPO_ROOT / "core" / "cli.py"), "stats", "--no-baseline"],
            capture_output=True, text=True, cwd=str(self.project_dir), env=env, check=True,
        )
        empty_dir = Path(self.temp_dir.name) / "empty-cwd"
        empty_dir.mkdir()
        req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "open_dashboard", "arguments": {}}}) + "\n"
        res = subprocess.run(
            [sys.executable, str(REPO_ROOT / "core" / "cli.py"), "mcp"],
            input=req, capture_output=True, text=True, encoding="utf-8", cwd=str(empty_dir), env=env, timeout=15,
        )
        self.assertEqual(res.returncode, 0, msg=res.stderr)
        sc = json.loads(res.stdout.splitlines()[0])["result"]["structuredContent"]
        self.assertEqual(sc["scope"], "all")
        self.assertEqual(sc["metrics"]["total_facts"], 1)


if __name__ == "__main__":
    unittest.main()
