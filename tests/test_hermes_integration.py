"""Integration tests for the Hermes shell hooks (JSON on stdin, JSON on stdout)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestHermesIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = self.tmp.name
        (Path(self.dir) / ".agents").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, args, payload=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("CENTSIBLE_")}
        env["CENTSIBLE_HOME"] = self.dir
        return subprocess.run([sys.executable, str(REPO_ROOT / "core" / "cli.py")] + args,
                              input=json.dumps(payload) if payload is not None else None,
                              capture_output=True, text=True, cwd=self.dir, env=env)

    def _hook(self, sub, payload):
        res = self._run(["hook", sub], payload)
        self.assertEqual(res.returncode, 0, res.stderr)
        return json.loads(res.stdout.strip().splitlines()[-1])

    def _payload(self, event, session, tool_name=None, tool_input=None, **extra):
        return {"hook_event_name": event, "tool_name": tool_name, "tool_input": tool_input,
                "session_id": session, "cwd": self.dir, "profile": "default", "extra": extra}

    def test_prompt_injects_context(self):
        self._run(["facts", "add", "--pattern", "deploy_staging", "--fact", "Run 'make deploy_staging ENV=uat'.",
                   "--category", "workflow", "--id", "f-deploy"])
        out = self._hook("hermes-prompt", self._payload("pre_llm_call", "h1", user_message="please deploy_staging now"))
        self.assertIn("make deploy_staging ENV=uat", out["context"])
        # multimodal turn: list of content parts
        out = self._hook("hermes-prompt", self._payload("pre_llm_call", "h1b",
                                                         user_message=[{"type": "text", "text": "deploy_staging"}]))
        self.assertIn("context", out)
        self.assertEqual(self._hook("hermes-prompt", self._payload("pre_llm_call", "h2", user_message="hello")), {})

    def test_pre_tool_blocks_identical_loop(self):
        p = self._payload("pre_tool_call", "h-loop", "terminal", {"command": "npm run build"})
        outs = [self._hook("hermes-pre-tool", p) for _ in range(4)]
        self.assertEqual(outs[:3], [{}, {}, {}])
        self.assertEqual(outs[3]["decision"], "block")
        self.assertIn("LoopGuard", outs[3]["reason"])

    def test_post_tool_reads_result_json_and_verify_nudges_once(self):
        fail = json.dumps({"output": "boom", "exit_code": 1, "error": None})
        for i in range(2):
            self._hook("hermes-post-tool", self._payload("post_tool_call", "h-post", "terminal",
                                                         {"command": f"pytest -x t{i}"}, result=fail))
        ok = json.dumps({"output": "Exit code: 3 in a log line", "exit_code": 0, "error": None})
        self._hook("hermes-post-tool", self._payload("post_tool_call", "h-post", "terminal", {"command": "ls"}, result=ok))
        first = self._hook("hermes-verify", self._payload("pre_verify", "h-post", attempt=0, coding=True))
        self.assertEqual(first["action"], "continue")
        self.assertIn("2 tool failure(s)", first["message"])
        self.assertEqual(self._hook("hermes-verify", self._payload("pre_verify", "h-post", attempt=0)), {})
        self.assertEqual(self._hook("hermes-verify", self._payload("pre_verify", "h-other", attempt=1)), {})

    def test_install_prints_hooks_block(self):
        res = self._run(["install", "--hermes"])
        self.assertEqual(res.returncode, 0)
        for event, sub in (("pre_llm_call", "hermes-prompt"), ("pre_tool_call", "hermes-pre-tool"),
                           ("post_tool_call", "hermes-post-tool"), ("pre_verify", "hermes-verify")):
            self.assertIn(f"  {event}:", res.stdout)
            self.assertIn(f"hook {sub}'", res.stdout)


if __name__ == "__main__":
    unittest.main()
