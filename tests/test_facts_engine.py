#!/usr/bin/env python3
"""
Unit tests for FactsEngine.
"""

import os
import tempfile
import unittest
from pathlib import Path

from core.facts_engine import MAX_FACT_CHARS, FactsEngine, bound_wildcards, get_default_facts_path, lint_pattern


class TestFactsEngine(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_path = Path(self.temp_dir.name) / "facts.jsonl"
        self.engine = FactsEngine(self.storage_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_add_and_list_facts(self):
        self.engine.add_fact("pytest|test", "Run pytest with -q flag", category="test", fact_id="test-1", force=True)
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
        self.engine.add_fact("bruno|api", "Use --condense flag for Bruno", fact_id="bruno-1", force=True)
        self.engine.add_fact("docker", "Use docker compose v2", fact_id="docker-1", force=True)

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

    def test_wildcards_are_bounded_on_long_prompts(self):
        self.engine.add_fact("pooling.*cancel.*idle", "pooling fact", fact_id="p", force=True)
        near = "pooling: cancel when idle"
        far = "pooling " + "x" * 500 + " cancel " + "y" * 500 + " idle"
        self.assertEqual(len(self.engine.match(near, record_hits=False)), 1)
        self.assertEqual(len(self.engine.match(far, record_hits=False)), 0)

    def test_bound_wildcards_leaves_escapes_and_classes(self):
        self.assertEqual(bound_wildcards(r"a\.*b"), r"a\.*b")
        self.assertEqual(bound_wildcards("[.*]x"), "[.*]x")
        self.assertEqual(bound_wildcards("[^]a.*]x.+"), "[^]a.*]x.{1,80}")
        self.assertEqual(bound_wildcards("a.*?b"), "a.{0,80}?b")

    def test_pattern_matching_empty_text_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.add_fact(".*", "fires everywhere")
        self.engine.add_fact(".*", "ok", category="always", fact_id="al")

    def test_format_ephemeral_message(self):
        self.engine.add_fact("bruno", "Fact A", fact_id="a", force=True)
        matched = self.engine.match("bruno")
        msg = self.engine.format_ephemeral_message(matched)
        self.assertIsNotNone(msg)
        self.assertIn("[CENTSIBLE MICRO-SKILLS: Active Repository Facts]", msg)
        self.assertIn("- Fact A", msg)

        # Empty match returns None
        self.assertIsNone(self.engine.format_ephemeral_message([]))

    def test_remove_fact(self):
        self.engine.add_fact("test-pattern", "Fact to remove", fact_id="rem-1", force=True)
        self.assertTrue(self.engine.remove_fact("rem-1"))
        self.assertEqual(len(self.engine.list_facts()), 0)

        # Non-existing returns False
        self.assertFalse(self.engine.remove_fact("non-existing"))

    def test_match_tool_call_missing_vs_present_flags(self):
        self.engine.add_fact(
            "bruno",
            "Append --condense to Bruno commands on UAT",
            fact_id="bruno-condense",
            force=True,
        )
        # Missing --condense -> triggers tool hint
        missing_call = {"name": "run_command", "args": {"CommandLine": "bruno run uat"}}
        matched = self.engine.match_tool_call(missing_call)
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["id"], "bruno-condense")

        # Already has --condense -> no redundant hint
        valid_call = {"name": "run_command", "args": {"CommandLine": "bruno run uat --condense"}}
        matched_valid = self.engine.match_tool_call(valid_call)
        self.assertEqual(len(matched_valid), 0)

        # Already injected in prompt (exclude_ids) -> skipped
        matched_excluded = self.engine.match_tool_call(missing_call, exclude_ids=["bruno-condense"])
        self.assertEqual(len(matched_excluded), 0)

    def test_auto_learn_from_recovery_and_metrics_roi(self):
        learned = self.engine.auto_learn_from_recovery("pytest tests/", "python3 -m pytest tests/ -q")
        self.assertIsNotNone(learned)
        self.assertEqual(learned["category"], "auto-learned")
        self.assertIn("python3 -m pytest tests/ -q", learned["fact"])

        # Trigger a hit and verify ROI summary
        self.engine.match("Please run pytest now")
        summary = self.engine.get_metrics_summary(usd_per_mtok=3.0)
        self.assertEqual(summary["total_facts"], 1)
        self.assertEqual(summary["auto_learned_facts"], 1)
        self.assertEqual(summary["fact_hits"], 1)
        self.assertGreater(summary["net_saved_tokens"], 1400)
        self.assertGreater(summary["saved_cents"], 0.4)
        self.assertGreater(summary["roi_multiplier"], 10.0)

    def test_auto_learn_handles_wrappers_and_compound_commands(self):
        cases = [
            ("cd /x && pytest tests/", "cd /x && python3 -m pytest tests/ -q", "pytest"),
            ("rtk pytest tests", "rtk proxy pytest tests -q", "pytest"),
            ("npm test", "npm run test:ci", "npm"),
            ("vitest", "npx vitest --run", "vitest"),
            ("pytest tests/", "uv run pytest tests/", "pytest"),
            ("tsc", "npx tsc --noEmit", "tsc"),
            ("make build", "make build -j4", "make"),
        ]
        for failed, ok, expected in cases:
            learned = self.engine.auto_learn_from_recovery(failed, ok)
            self.assertIsNotNone(learned, failed)
            self.assertEqual(learned["pattern"], expected)
        # Different tools or no known tool -> nothing learned
        self.assertIsNone(self.engine.auto_learn_from_recovery("pytest x", "npm test"))
        self.assertIsNone(self.engine.auto_learn_from_recovery("./foo a", "./foo b"))
        # Only target path or working directory changed -> not a CLI recovery
        self.assertIsNone(self.engine.auto_learn_from_recovery("pytest tests/a.py", "pytest tests/b.py"))
        self.assertIsNone(self.engine.auto_learn_from_recovery("cd /a && pytest", "cd /b && pytest"))
        # Auto-learned fact with "instead of '...'" does not demand the failed command in PreToolUse
        self.engine.auto_learn_from_recovery("pytest tests/ --bad-flag", "pytest tests/ -q")
        valid_call = {"name": "Bash", "args": {"command": "pytest tests/ -q"}}
        self.assertEqual(self.engine.match_tool_call(valid_call), [])

    def test_auto_learn_from_recovery_on_interpreter_scripts(self):
        cases = [
            ("node scripts/run-bruno.js folder", "node scripts/run-bruno.js folder --env UAT", "run-bruno.js"),
            ("python3 scripts/query_argus.py abc", "python3 scripts/query_argus.py abc --env prod", "query_argus.py"),
        ]
        for failed, ok, expected in cases:
            learned = self.engine.auto_learn_from_recovery(failed, ok)
            self.assertIsNotNone(learned, failed)
            self.assertEqual(learned["pattern"], expected)
        # Only the script's target argument changed -> not a recovery; plain interpreter w/o script ignored
        self.assertIsNone(self.engine.auto_learn_from_recovery("node scripts/a.js d/x.json", "node scripts/a.js d/y.json"))
        self.assertIsNone(self.engine.auto_learn_from_recovery("node -e 1", "node -e 2"))

    def test_auto_learn_from_correction_and_file_gotcha_matching(self):
        # 1. Questions must never be treated as corrections
        self.assertIsNone(self.engine.auto_learn_from_correction("tại sao lượt auto learn chỉ có 1 vậy"))
        self.assertIsNone(self.engine.auto_learn_from_correction("có nên mở rộng fact cho llm context không?"))

        # 2. Explicit user correction with file/module targets auto-learns a 'gotcha'
        learned = self.engine.auto_learn_from_correction(
            "Đừng sửa file legacy_auth.py, bắt buộc phải sửa ở jwt_middleware.py"
        )
        self.assertIsNotNone(learned)
        self.assertEqual(learned["category"], "gotcha")
        self.assertEqual(learned["pattern"], "legacy_auth.py|jwt_middleware.py")

        # 3. File tool (Edit/Read) touching legacy_auth.py triggers the gotcha mid-turn without CLI flags
        edit_call = {"name": "Edit", "args": {"file_path": "/workspace/src/legacy_auth.py"}}
        matched = self.engine.match_tool_call(edit_call)
        self.assertEqual(len(matched), 1)
        self.assertEqual(matched[0]["id"], learned["id"])

    def test_vietnamese_nfc_nfd_and_accent_insensitive_matching(self):
        import unicodedata

        self.engine.add_fact(
            "kiểm thử|chạy đơ",
            "Dùng 'pytest -q' trên môi trường 'thử nghiệm'",
            fact_id="vn-test",
            force=True,
        )
        # 1. Exact NFC Vietnamese match
        self.assertEqual(len(self.engine.match("Hãy chạy kiểm thử giúp tôi")), 1)
        # 2. macOS NFD decomposed Unicode match
        nfd_prompt = unicodedata.normalize("NFD", "Hãy chạy kiểm thử giúp tôi")
        self.assertEqual(len(self.engine.match(nfd_prompt)), 1)
        # 3. Non-diacritic (không dấu, gồm cả đ -> d) fallback match
        self.assertEqual(len(self.engine.match("Hay chay kiem thu va chay do giup toi")), 1)

    def test_get_default_facts_path_resolution(self):
        with tempfile.TemporaryDirectory() as ws_dir, tempfile.TemporaryDirectory() as global_home:
            ws_path = Path(ws_dir).resolve()
            old_env = os.environ.get("CENTSIBLE_HOME")
            old_cwd = Path.cwd()
            try:
                os.environ["CENTSIBLE_HOME"] = global_home
                os.chdir(ws_path)

                # 1. Neither .agents nor .claude exists -> per-project global path (never shared across repos)
                resolved_global = get_default_facts_path([str(ws_path)])
                self.assertEqual(resolved_global.parent.parent, Path(global_home).resolve() / "projects")
                self.assertEqual(resolved_global.name, "facts.jsonl")
                with tempfile.TemporaryDirectory() as other_dir:
                    self.assertNotEqual(resolved_global, get_default_facts_path([other_dir]))
                self.assertFalse(resolved_global.parent.exists())  # resolving must not create dirs

                # 2. Only .claude exists -> project .claude/facts.jsonl
                claude_dir = ws_path / ".claude"
                claude_dir.mkdir()
                resolved_claude = get_default_facts_path([str(ws_path)])
                self.assertEqual(resolved_claude, claude_dir / "facts.jsonl")

                # 3. Both .agents and .claude exist -> .agents/facts.jsonl takes precedence
                agents_dir = ws_path / ".agents"
                agents_dir.mkdir()
                resolved_agents = get_default_facts_path([str(ws_path)])
                self.assertEqual(resolved_agents, agents_dir / "facts.jsonl")
            finally:
                os.chdir(old_cwd)
                if old_env is None:
                    os.environ.pop("CENTSIBLE_HOME", None)
                else:
                    os.environ["CENTSIBLE_HOME"] = old_env

    def test_plain_patterns_match_whole_tokens_only(self):
        self.engine.add_fact("pip", "use uv pip", fact_id="p", force=True)
        self.engine.add_fact("auth/", "auth rule", fact_id="a", force=True)
        self.engine.add_fact("jest|vitest", "run via npx", fact_id="j", force=True)
        hit = lambda t: sorted(f["id"] for f in self.engine.match(t, record_hits=False))
        self.assertEqual(hit("stdin=subprocess.PIPE"), [])
        self.assertEqual(hit("pipeline stage"), [])
        self.assertEqual(hit("install with pip"), ["p"])
        self.assertEqual(hit("run pip install"), ["p"])
        self.assertEqual(hit("edit auth/login.py"), ["a"])  # trailing '/' edge gets no boundary
        self.assertEqual(hit("vitest watch"), ["j"])
        self.assertEqual(hit("jestify"), [])


class TestGlobalFacts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.engine = FactsEngine(d / "proj" / "facts.jsonl", global_path=d / "glob" / "facts.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def test_global_fact_visible_in_every_project_but_stored_once(self):
        self.engine.add_fact("rtk", "prefix shell with rtk", fact_id="g1", scope="global", force=True)
        other = FactsEngine(Path(self.tmp.name) / "other" / "facts.jsonl", global_path=self.engine.global_path)
        self.assertEqual([f["id"] for f in other.match("use rtk here", record_hits=False)], ["g1"])
        self.assertEqual(self.engine.list_facts(scope="project"), [])  # never copied into the project file

    def test_project_fact_overrides_global_with_same_pattern(self):
        self.engine.add_fact("pytest", "global way", fact_id="g", scope="global", force=True)
        self.engine.add_fact("pytest", "project way", fact_id="p", force=True)
        self.assertEqual([f["fact"] for f in self.engine.match("run pytest", record_hits=False)], ["project way"])

    def test_hits_persist_to_global_file_and_overridden_global_survives(self):
        self.engine.add_fact("rtk", "use rtk", fact_id="g1", scope="global", force=True)
        self.engine.add_fact("pytest", "global pytest", fact_id="g2", scope="global", force=True)
        self.engine.add_fact("pytest", "project pytest", fact_id="p", force=True)
        self.engine.match("rtk and pytest")  # records hits for g1 (global) and p (project)
        glob = {f["id"]: f for f in self.engine.list_facts(scope="global")}
        self.assertEqual(glob["g1"]["hits"], 1)
        self.assertIn("g2", glob)  # shadowed by project fact but must not be deleted
        self.assertEqual(self.engine.list_facts(scope="project")[0]["hits"], 1)
        self.assertNotIn("_scope", self.engine.list_facts(scope="global")[0])

    def test_remove_is_scoped(self):
        self.engine.add_fact("rtk", "use rtk", fact_id="g1", scope="global", force=True)
        self.assertFalse(self.engine.remove_fact("g1"))  # project scope by default
        self.assertTrue(self.engine.remove_fact("g1", scope="global"))

    def test_global_scope_without_global_file_is_an_error(self):
        solo = FactsEngine(Path(self.tmp.name) / "solo" / "facts.jsonl")
        with self.assertRaises(ValueError):
            solo.add_fact("x", "y", scope="global", force=True)

    def test_rewrite_keeps_comments_and_unparsable_lines(self):
        self.engine.add_fact("foo", "fact A", fact_id="a", force=True)
        path = self.engine.storage_path
        path.write_text("# my note\n" + path.read_text() + "{corrupt\n")
        self.engine.match("foo")  # records a hit -> rewrites the file
        text = path.read_text()
        self.assertIn("# my note", text)
        self.assertIn("{corrupt", text)
        self.assertEqual([f["id"] for f in self.engine.list_facts()], ["a"])

    def test_same_pattern_different_fact_is_kept_but_identical_is_idempotent(self):
        self.engine.add_fact("pytest", "run X", force=True)
        self.engine.add_fact("pytest", "run Y", force=True)
        self.engine.add_fact("pytest", "run Y", force=True)
        self.assertEqual(sorted(f["fact"] for f in self.engine.list_facts()), ["run X", "run Y"])

    def test_auto_gotchas_about_same_file_do_not_overwrite(self):
        a = self.engine.auto_learn_from_correction("đừng sửa cli.py vì nó sinh tự động")
        b = self.engine.auto_learn_from_correction("lưu ý là cli.py luôn phải chạy test sau khi sửa")
        self.assertNotEqual(a["id"], b["id"])
        self.assertEqual(len(self.engine.list_facts()), 2)
        self.assertEqual(self.engine.auto_learn_from_correction("đừng sửa cli.py vì nó sinh tự động")["id"], a["id"])
        self.assertEqual(len(self.engine.list_facts()), 2)

    def test_redact_secrets(self):
        from core.facts_engine import redact_secrets
        for raw in (
            "run --token=sk-SECRET123456 x", "run --password hunter2 x", "API_TOKEN=abc123 run",
            "curl -H 'Authorization: Bearer abc.def.ghi' u", "git clone https://bob:pw@host/r.git",
            "key ghp_" + "a" * 24, "AWS AKIAABCDEFGHIJKLMNOP",
        ):
            out = redact_secrets(raw)
            for leak in ("SECRET123456", "hunter2", "abc123", "abc.def.ghi", "pw@", "a" * 24, "AKIAABCDEFGHIJKLMNOP"):
                self.assertNotIn(leak, out, raw)
        self.assertEqual(redact_secrets("đừng sửa auth/login.py, auth module hỏng"), "đừng sửa auth/login.py, auth module hỏng")

    def test_auto_learned_facts_never_store_secrets(self):
        learned = self.engine.auto_learn_from_recovery("pytest tests/", "uv run pytest -q --token=sk-SECRET123456 tests/")
        self.assertNotIn("SECRET123456", learned["fact"])
        gotcha = self.engine.auto_learn_from_correction("đừng sửa cli.py khi API_KEY=abcdef123 còn trong env")
        self.assertNotIn("abcdef123", gotcha["fact"])


class TestPatternSpecificity(unittest.TestCase):
    """Saved facts are injected into prompts, so a pattern must describe the situation, not one keyword."""

    LOOSE = [
        "pip", "rtk", "pytest|rtk", "argus", "bruno",
        "(run.*test|test.*suite|chạy.*test)",
        "(tạo đơn|create.*order|book đơn)",
        "pip.*", "\\bpip\\b",
    ]
    SPECIFIC = [
        "query_argus", "fast-e2e", "troubleshoot_cases.db", "finalize_cs_case.py", "auth/",
        "supplier_transaction|payment/page/supplier",
        "npm test|unittest",
        "pytest.{0,40}(spawn|fail|lỗi|rtk)",
        "(fast-e2e|(tạo|lên|book|create).{0,12}(đơn|order).{0,40}(uat|stg|test|e2e|84[0-9]{8,}))",
        "*",
    ]

    def test_lint_rejects_keyword_patterns_and_accepts_situation_patterns(self):
        for pattern in self.LOOSE:
            self.assertIsNotNone(lint_pattern(pattern), pattern)
        for pattern in self.SPECIFIC:
            self.assertIsNone(lint_pattern(pattern), pattern)

    def test_one_loose_alternative_is_enough_to_reject(self):
        self.assertIsNotNone(lint_pattern("query_argus|argus"))

    def test_add_fact_rejects_loose_pattern_unless_forced_or_exempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = FactsEngine(Path(tmp) / "facts.jsonl")
            with self.assertRaisesRegex(ValueError, "too loose"):
                engine.add_fact("pytest", "run with -q")
            self.assertEqual(engine.list_facts(), [])
            engine.add_fact("pytest", "run with -q", force=True)
            engine.add_fact("pip", "use uv pip", category="auto-learned", fact_id="auto_pip")
            self.assertEqual(len(engine.list_facts()), 2)

    def test_add_fact_rejects_overlong_fact(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = FactsEngine(Path(tmp) / "facts.jsonl")
            with self.assertRaisesRegex(ValueError, "max"):
                engine.add_fact("query_argus", "x" * (MAX_FACT_CHARS + 1))
            engine.add_fact("query_argus", "x" * MAX_FACT_CHARS)

    def test_saved_wildcards_are_bounded_tighter_than_match_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = FactsEngine(Path(tmp) / "facts.jsonl")
            entry = engine.add_fact("pytest.*(fail|error)", "use -q")
            self.assertEqual(entry["pattern"], "pytest.{0,40}(fail|error)")

    def test_auto_learn_skips_pasted_command_chains(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = FactsEngine(Path(tmp) / "facts.jsonl")
            long_ok = "pip install " + " ".join(f"pkg{i}" for i in range(60))
            self.assertIsNone(engine.auto_learn_from_recovery("pip list", long_ok))
            self.assertEqual(engine.list_facts(), [])

    def test_specificity_cases_from_the_qc_repo(self):
        """Prompts the old keyword patterns wrongly fired on must not match the tightened ones."""
        with tempfile.TemporaryDirectory() as tmp:
            engine = FactsEngine(Path(tmp) / "facts.jsonl")
            engine.add_fact(
                "(fast-e2e|(tạo|lên|book|create).{0,12}(đơn|order).{0,40}(uat|stg|test|e2e|84[0-9]{8,}|điểm giao))",
                "Use scripts/fast-e2e.py", fact_id="order",
            )
            for prompt in ("tạo đơn là gì", "chạy test case login trên mobile", "rtk là gì"):
                self.assertEqual(engine.match(prompt, record_hits=False), [], prompt)
            for prompt in ("tạo đơn UAT cho khách 84909000003", "lên đơn 2 điểm giao"):
                self.assertEqual(len(engine.match(prompt, record_hits=False)), 1, prompt)


if __name__ == "__main__":
    unittest.main()
