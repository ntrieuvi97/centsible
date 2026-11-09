"""Install the centsible CLI symlink into the user PATH."""

import os
import sys
from pathlib import Path
from typing import Optional

# Ensure repo root is in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.facts_engine import debug_warn


REPO_ROOT = Path(__file__).resolve().parent.parent


def ensure_cli_symlink() -> Optional[Path]:
    """Ensure ~/.local/bin/centsible symlink (or .cmd on Windows) points to a valid binary so CLI works out-of-the-box."""
    try:
        target_bin = REPO_ROOT / "bin" / "centsible"
        if not target_bin.exists():
            return None

        # Ensure executable bit on Unix
        if os.name != "nt":
            try:
                st = target_bin.stat()
                if not (st.st_mode & 0o111):
                    target_bin.chmod(st.st_mode | 0o755)
            except OSError:
                pass

        local_bin = Path.home() / ".local" / "bin"
        local_bin.mkdir(parents=True, exist_ok=True)

        if os.name == "nt":
            cmd_path = local_bin / "centsible.cmd"
            if not cmd_path.exists():
                cmd_content = f'@echo off\r\npython "{target_bin.resolve()}" %*\r\n'
                cmd_path.write_text(cmd_content, encoding="utf-8")
            return cmd_path

        link_path = local_bin / "centsible"
        if link_path.is_symlink():
            try:
                if link_path.resolve() == target_bin.resolve():
                    return link_path
            except OSError:
                pass
            link_path.unlink()
        elif link_path.exists():
            try:
                if link_path.samefile(target_bin):
                    return link_path
            except OSError:
                pass
            link_path.unlink()

        link_path.symlink_to(target_bin)
        return link_path
    except Exception as e:
        debug_warn("ensure_cli_symlink", e)
        return None


def handle_install(dry_run: bool = False) -> int:
    """Install or symlink centsible to ~/.local/bin and ensure PATH is configured."""
    print("=== Centsible CLI Installation 🪙 ===")
    target_bin = REPO_ROOT / "bin" / "centsible"
    if not target_bin.exists():
        print(f"✗ Cannot locate launcher at: {target_bin}", file=sys.stderr)
        return 1

    # 1. Ensure executable permission on Unix
    if os.name != "nt":
        try:
            st = target_bin.stat()
            if not (st.st_mode & 0o111):
                if not dry_run:
                    target_bin.chmod(st.st_mode | 0o755)
                print(f"✓ Ensured executable bit on {target_bin}")
        except OSError as e:
            print(f"⚠ Could not chmod launcher: {e}")

    # 2. Setup target dir and link or batch wrapper
    local_bin = Path.home() / ".local" / "bin"
    if not dry_run:
        local_bin.mkdir(parents=True, exist_ok=True)
    print(f"• Target directory: {local_bin}")

    if os.name == "nt":
        cmd_path = local_bin / "centsible.cmd"
        cmd_content = f'@echo off\r\npython "{target_bin.resolve()}" %*\r\n'
        if not dry_run:
            cmd_path.write_text(cmd_content, encoding="utf-8")
        print(f"✓ Created Windows batch wrapper at {cmd_path}")
    else:
        link_path = local_bin / "centsible"
        need_link = True
        if link_path.is_symlink():
            try:
                if link_path.resolve() == target_bin.resolve():
                    need_link = False
            except OSError:
                pass
            if need_link and not dry_run:
                link_path.unlink()
        elif link_path.exists():
            try:
                if link_path.samefile(target_bin):
                    need_link = False
            except OSError:
                pass
            if need_link and not dry_run:
                link_path.unlink()

        if need_link:
            if not dry_run:
                link_path.symlink_to(target_bin)
            print(f"✓ Created symlink: {link_path} -> {target_bin}")
        else:
            print(f"✓ Symlink already points to: {target_bin}")

    # 3. Check PATH
    env_path = os.environ.get("PATH", "")
    in_path = False
    for p in env_path.split(os.pathsep):
        try:
            if Path(p).resolve() == local_bin.resolve():
                in_path = True
                break
        except Exception:
            continue

    if in_path:
        print(f"✓ {local_bin} is in your current PATH.")
    else:
        print(f"⚠ {local_bin} is NOT in your current PATH.")
        shell = os.environ.get("SHELL", "")
        rc_file = None
        if "zsh" in shell:
            rc_file = Path.home() / ".zshrc"
        elif "bash" in shell:
            rc_file = Path.home() / ".bashrc"
        elif (Path.home() / ".profile").exists():
            rc_file = Path.home() / ".profile"
        elif (Path.home() / ".bashrc").exists():
            rc_file = Path.home() / ".bashrc"
        elif (Path.home() / ".zshrc").exists():
            rc_file = Path.home() / ".zshrc"

        export_line = 'export PATH="$HOME/.local/bin:$PATH"'
        if rc_file and rc_file.exists():
            try:
                content = rc_file.read_text(encoding="utf-8", errors="ignore")
                if export_line not in content and ".local/bin" not in content:
                    if not dry_run:
                        with open(rc_file, "a", encoding="utf-8") as f:
                            f.write(f"\n# Added by centsible installer\n{export_line}\n")
                    print(f"✓ Added PATH export to {rc_file}")
                else:
                    print(f"• PATH export found in {rc_file}")
            except Exception as e:
                print(f"⚠ Could not update {rc_file}: {e}")

        print("\n👉 To activate in your current terminal session, run:")
        print(f"   {export_line}")
        print("   (or open a new terminal tab)\n")

    print("🎉 Centsible installation complete!")
    print("Test your install with: centsible status")
    return 0
