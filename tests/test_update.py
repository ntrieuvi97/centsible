"""`centsible update`: install detection and per-install update plans (no network, no real updates)."""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import core.update as upd


def _fake_install(root: Path, version: str) -> Path:
    (root / "core").mkdir(parents=True, exist_ok=True)
    (root / "core" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    return root


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


class TestUpdate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "home"
        self.home.mkdir()
        self.patches = [mock.patch.object(upd, "_home", return_value=self.home),
                        mock.patch.object(upd, "latest_pypi_version", return_value="0.10.1")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_versions_compare_numerically(self):
        self.assertLess(upd.parse_version("0.9.0"), upd.parse_version("0.10.0"))
        self.assertEqual(upd.parse_version("garbage"), (0,))

    def test_detects_pip_gemini_clone_and_claude_plugin(self):
        site = _fake_install(Path(self.tmp.name) / "site-packages", "0.10.0")
        clone = _fake_install(self.home / ".gemini" / "config" / "plugins" / "centsible", "0.9.0")
        (clone / ".git").mkdir()
        plugins = self.home / ".claude" / "plugins"
        plugins.mkdir(parents=True)
        (plugins / "installed_plugins.json").write_text(json.dumps({"plugins": {
            "centsible@centsible": [{"version": "0.9.0"}, {"version": "0.10.0"}],
            "other@centsible": [{"version": "1.0.0"}],
        }}))
        found = upd.detect_installs(site)
        self.assertEqual([(i["kind"], i["current"]) for i in found],
                         [("pip", "0.10.0"), ("git", "0.9.0"), ("claude-plugin", "0.10.0")])

    def test_claude_plugin_cache_is_not_treated_as_pip(self):
        cache = _fake_install(self.home / ".claude" / "plugins" / "cache" / "centsible" / "centsible" / "0.10.0", "0.10.0")
        self.assertEqual(upd.detect_installs(cache), [])

    def test_plans(self):
        self.assertEqual(upd.plan_steps({"kind": "pip"})[0][0][-3:], ["install", "--upgrade", "centsible"])
        with mock.patch.object(upd.shutil, "which", return_value=None):
            steps, skip = upd.plan_steps({"kind": "claude-plugin", "id": "centsible@centsible"})
        self.assertEqual(steps, [])
        self.assertIn("/plugin update", skip)
        with mock.patch.object(upd.shutil, "which", return_value="/bin/claude"):
            steps, _ = upd.plan_steps({"kind": "claude-plugin", "id": "centsible@mymarket"})
        self.assertEqual(steps, [["/bin/claude", "plugin", "marketplace", "update", "mymarket"],
                                 ["/bin/claude", "plugin", "update", "centsible@mymarket"]])

    def test_git_checkout_with_local_edits_is_skipped(self):
        repo = _fake_install(Path(self.tmp.name) / "repo", "0.9.0")
        _git(repo, "init", "-q")
        _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
        _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init")
        self.assertEqual(upd.plan_steps({"kind": "git", "path": str(repo)}),
                         ([["git", "-C", str(repo), "pull", "--ff-only"]], None))
        (repo / "core" / "__init__.py").write_text('__version__ = "local"\n')
        steps, skip = upd.plan_steps({"kind": "git", "path": str(repo)})
        self.assertEqual(steps, [])
        self.assertIn("uncommitted changes", skip)

    def test_up_to_date_pip_runs_nothing(self):
        site = _fake_install(Path(self.tmp.name) / "site-packages", "0.10.1")
        with mock.patch.object(upd.subprocess, "run") as run:
            self.assertEqual(upd.run_update(root=site), 0)
        run.assert_not_called()

    def test_check_never_runs_commands(self):
        site = _fake_install(Path(self.tmp.name) / "site-packages", "0.9.0")
        with mock.patch.object(upd.subprocess, "run") as run:
            self.assertEqual(upd.run_update(check=True, root=site), 0)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
