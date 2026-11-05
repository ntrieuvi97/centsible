#!/usr/bin/env python3
"""The version is hand-copied into several files; fail loudly when they drift."""

import json
import re
import unittest
from pathlib import Path

from core import __version__

ROOT = Path(__file__).resolve().parent.parent


class TestVersionSync(unittest.TestCase):
    def test_all_manifests_match_package_version(self):
        found = {
            "plugin.json": json.loads((ROOT / "plugin.json").read_text())["version"],
            ".claude-plugin/plugin.json": json.loads((ROOT / ".claude-plugin/plugin.json").read_text())["version"],
            ".claude-plugin/marketplace.json": json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())["plugins"][0]["version"],
            "README.md": re.search(r"Version-([\d.]+)-", (ROOT / "README.md").read_text()).group(1),
        }
        self.assertEqual({k: v for k, v in found.items() if v != __version__}, {})


if __name__ == "__main__":
    unittest.main()
