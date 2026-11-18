"""`centsible update`: find every centsible install on this machine and bring each one to the latest release.

Installs are detected, not configured:
  - git checkout (install.sh, Antigravity clone, a dev checkout)  -> git pull --ff-only
  - pip / PyPI package (site-packages)                            -> python -m pip install --upgrade centsible
  - Claude Code plugin (~/.claude/plugins/installed_plugins.json) -> claude plugin marketplace update + plugin update
"""

import json
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPI_JSON_URL = "https://pypi.org/pypi/centsible/json"
PACKAGE = "centsible"
TIMEOUT = 120


def _home() -> Path:
    return Path.home()


def _version_at(root: Path) -> str:
    """__version__ of the install rooted at `root` (read from disk, so it reflects an update just applied)."""
    try:
        m = re.search(r'__version__\s*=\s*"([^"]+)"', (root / "core" / "__init__.py").read_text(encoding="utf-8"))
        return m.group(1) if m else "?"
    except OSError:
        return "?"


def parse_version(v: str) -> Tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", v)[:3]) or (0,)


def latest_pypi_version(timeout: float = 5.0) -> Optional[str]:
    """Latest version on PyPI, or None when offline / unreachable."""
    try:
        with urllib.request.urlopen(PYPI_JSON_URL, timeout=timeout) as resp:
            return str(json.load(resp)["info"]["version"])
    except Exception:
        return None


def _claude_plugin_ids() -> List[str]:
    """Installed Claude Code plugin ids that are centsible (`centsible@<marketplace>`)."""
    path = _home() / ".claude" / "plugins" / "installed_plugins.json"
    try:
        plugins = json.loads(path.read_text(encoding="utf-8")).get("plugins", {})
    except (OSError, ValueError, AttributeError):
        return []
    return sorted(pid for pid in plugins if isinstance(pid, str) and pid.split("@", 1)[0] == PACKAGE)


def _is_claude_cache(root: Path) -> bool:
    try:
        root.resolve().relative_to((_home() / ".claude" / "plugins").resolve())
        return True
    except ValueError:
        return False


def detect_installs(root: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Every install this command can update, the running one first. Each: {kind, path|id, current}."""
    root = (root or REPO_ROOT).resolve()
    installs: List[Dict[str, Any]] = []
    seen_paths = set()

    def add_git(path: Path) -> None:
        p = path.resolve()
        if p not in seen_paths and (p / ".git").exists() and (p / "core" / "__init__.py").is_file():
            seen_paths.add(p)
            installs.append({"kind": "git", "path": str(p), "current": _version_at(p)})

    if (root / ".git").exists():
        add_git(root)
    elif not _is_claude_cache(root):
        seen_paths.add(root)
        installs.append({"kind": "pip", "path": str(root), "current": _version_at(root)})
    add_git(_home() / ".gemini" / "config" / "plugins" / PACKAGE)  # install.sh / Antigravity default
    for pid in _claude_plugin_ids():
        installs.append({"kind": "claude-plugin", "id": pid, "current": _claude_plugin_version(pid)})
    return installs


def _claude_plugin_version(pid: str) -> str:
    path = _home() / ".claude" / "plugins" / "installed_plugins.json"
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))["plugins"][pid]
        return str(max((e.get("version", "0") for e in entries), key=parse_version))
    except (OSError, ValueError, KeyError, TypeError):
        return "?"


def plan_steps(inst: Dict[str, Any], latest: Optional[str] = None) -> Tuple[List[List[str]], Optional[str]]:
    """(commands to run, reason to skip). A git checkout with local edits is skipped, never overwritten.
    For pip, the known latest version is pinned and the cache bypassed: right after a release PyPI's simple index
    can lag its JSON API, and an unpinned upgrade then 'succeeds' without changing anything."""
    kind = inst["kind"]
    if kind == "git":
        path = inst["path"]
        try:
            dirty = subprocess.run(["git", "-C", path, "status", "--porcelain", "--untracked-files=no"],
                                   capture_output=True, text=True, timeout=30).stdout.strip()
        except (OSError, subprocess.SubprocessError) as e:
            return [], f"git not usable here ({e})"
        if dirty:
            return [], "has uncommitted changes; commit or stash them, then rerun"
        return [["git", "-C", path, "pull", "--ff-only"]], None
    if kind == "pip":
        spec = f"{PACKAGE}=={latest}" if latest else PACKAGE
        return [[sys.executable, "-m", "pip", "install", "--upgrade", "--no-cache-dir", spec]], None
    if kind == "claude-plugin":
        claude = shutil.which("claude")
        if not claude:
            return [], "the `claude` CLI is not on PATH; run `/plugin update` inside Claude Code instead"
        marketplace = inst["id"].split("@", 1)[1] if "@" in inst["id"] else PACKAGE
        return [[claude, "plugin", "marketplace", "update", marketplace], [claude, "plugin", "update", inst["id"]]], None
    return [], f"unknown install kind {kind}"


def _label(inst: Dict[str, Any]) -> str:
    return f"{inst['kind']:13} {inst.get('path') or inst.get('id')}"


def run_update(check: bool = False, dry_run: bool = False, root: Optional[Path] = None) -> int:
    installs = detect_installs(root)
    latest = latest_pypi_version()
    print("=== Centsible Update 🪙 ===")
    print(f"• Latest release: {latest or 'unknown (PyPI unreachable)'}")
    for inst in installs:
        behind = latest and inst["current"] != "?" and parse_version(inst["current"]) < parse_version(latest)
        print(f"• {_label(inst)}  v{inst['current']}" + ("  (update available)" if behind else ""))
    if check:
        return 0

    failed = 0
    for inst in installs:
        up_to_date = (inst["kind"] != "git" and latest and inst["current"] != "?"
                      and parse_version(inst["current"]) >= parse_version(latest))
        if up_to_date:
            print(f"\n✓ {_label(inst)}: already v{inst['current']}")
            continue
        steps, skip = plan_steps(inst, latest)
        if skip:
            print(f"\n⚠ Skipped {_label(inst)}: {skip}")
            failed += 1
            continue
        print(f"\n→ {_label(inst)}")
        for cmd in steps:
            print("  $ " + " ".join(cmd))
            if dry_run:
                continue
            try:
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
            except (OSError, subprocess.SubprocessError) as e:
                res = subprocess.CompletedProcess(cmd, 1, "", str(e))
            if res.returncode != 0:
                print("  ✗ failed: " + (res.stderr or res.stdout).strip()[-500:])
                failed += 1
                break
        else:
            if not dry_run:
                now = _after_version(inst)
                if latest and now != "?" and parse_version(now) < parse_version(latest):
                    print(f"  ✗ still v{now} (latest is v{latest}); PyPI can take a few minutes to serve a new "
                          "release, rerun `centsible update` shortly")
                    failed += 1
                else:
                    print(f"  ✓ now v{now}")
    if not dry_run and any(i["kind"] == "claude-plugin" for i in installs):
        print("\nRestart Claude Code to load the updated plugin.")
    return 1 if failed else 0


def _after_version(inst: Dict[str, Any]) -> str:
    if inst["kind"] == "claude-plugin":
        return _claude_plugin_version(inst["id"])
    return _version_at(Path(inst["path"]))  # pip upgrades in place, so the same site-packages path has the new file
