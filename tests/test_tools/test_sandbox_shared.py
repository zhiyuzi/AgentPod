"""Tests for shared layer bind-mount integration in sandbox/isolate.py and BashTool."""

from __future__ import annotations

import base64
import platform
from pathlib import Path

import pytest

from agentpod.sandbox.isolate import build_sandboxed_command, sandbox_available
from agentpod.tools.bash import BashTool

_IS_LINUX = platform.system() == "Linux"


# ---------------------------------------------------------------------------
# build_sandboxed_command tests
# ---------------------------------------------------------------------------


def test_no_sandbox_fallback(tmp_path: Path) -> None:
    """Non-Linux: build_sandboxed_command returns original command unchanged."""
    if _IS_LINUX and sandbox_available():
        pytest.skip("This test targets the non-sandbox fallback path")
    cmd, effective_cwd = build_sandboxed_command("echo hi", tmp_path)
    assert cmd == "echo hi"
    assert effective_cwd == tmp_path


def test_no_shared_dir_backward_compat(tmp_path: Path) -> None:
    """Without shared_dir the command must not contain any shared mount lines."""
    if not sandbox_available():
        pytest.skip("Linux sandbox only")
    cmd, _ = build_sandboxed_command("echo hi", tmp_path)
    # Without shared_dir, the only bind-mounts should be system dirs (/bin, /usr, /lib, etc.)
    # Count mount --bind occurrences — should match exactly the system dirs count
    from agentpod.sandbox.isolate import _BIND_MOUNT_DIRS
    # +1 for the self-bind of CWD (required for pivot_root)
    system_mount_count = len([d for d in _BIND_MOUNT_DIRS if d not in ("/dev",)]) + 1
    actual_bind_mounts = cmd.count("mount --bind")
    assert actual_bind_mounts == system_mount_count
    # The basic pivot_root structure must still be present
    assert "pivot_root" in cmd
    assert "unshare" in cmd


@pytest.mark.skipif(not _IS_LINUX, reason="Linux sandbox only")
def test_shared_dir_generates_bind_mounts(tmp_path: Path) -> None:
    """With shared_dir, generated command includes bind-mount for shared skills."""
    if not sandbox_available():
        pytest.skip("Linux sandbox only")

    shared = tmp_path / "shared"
    skill_dir = shared / ".agents" / "skills" / "my_skill"
    skill_dir.mkdir(parents=True)

    cwd = tmp_path / "user_cwd"
    cwd.mkdir()

    cmd, _ = build_sandboxed_command("echo hi", cwd, shared_dir=shared)

    assert "mount --bind" in cmd
    assert str(skill_dir) in cmd
    assert "my_skill" in cmd


@pytest.mark.skipif(not _IS_LINUX, reason="Linux sandbox only")
def test_user_skill_takes_priority(tmp_path: Path) -> None:
    """User's own skill dir prevents the shared skill from being mounted."""
    if not sandbox_available():
        pytest.skip("Linux sandbox only")

    shared = tmp_path / "shared"
    shared_skill = shared / ".agents" / "skills" / "common_skill"
    shared_skill.mkdir(parents=True)

    cwd = tmp_path / "user_cwd"
    user_skill = cwd / ".agents" / "skills" / "common_skill"
    user_skill.mkdir(parents=True)

    cmd, _ = build_sandboxed_command("echo hi", cwd, shared_dir=shared)

    # shared skill path must NOT appear in the mount commands
    assert str(shared_skill) not in cmd


@pytest.mark.skipif(not _IS_LINUX, reason="Linux sandbox only")
def test_exclude_list_respected(tmp_path: Path) -> None:
    """sessions, version, and .agents/cron must not be mounted from shared."""
    if not sandbox_available():
        pytest.skip("Linux sandbox only")

    shared = tmp_path / "shared"
    # Create excluded paths in shared
    (shared / "sessions").mkdir(parents=True)
    (shared / "version").write_text("1")
    (shared / ".agents" / "cron").mkdir(parents=True)
    # Also create a legitimate skill so shared_dir.is_dir() is satisfied
    (shared / ".agents" / "skills" / "ok_skill").mkdir(parents=True)

    cwd = tmp_path / "user_cwd"
    cwd.mkdir()

    cmd, _ = build_sandboxed_command("echo hi", cwd, shared_dir=shared)

    cwd_abs = str(cwd.resolve())
    # Excluded targets must not appear as mount destinations
    assert f"{cwd_abs}/sessions" not in cmd
    assert f"{cwd_abs}/version" not in cmd
    assert f"{cwd_abs}/.agents/cron" not in cmd
    # The legitimate skill should be present
    assert "ok_skill" in cmd


# ---------------------------------------------------------------------------
# BashTool tests
# ---------------------------------------------------------------------------


def test_bashtool_accepts_shared_dir(tmp_path: Path) -> None:
    """BashTool can be instantiated with a shared_dir argument."""
    shared = tmp_path / "shared"
    shared.mkdir()
    tool = BashTool(shared_dir=shared)
    assert tool.shared_dir == shared


def test_bashtool_default_no_shared_dir() -> None:
    """BashTool defaults to shared_dir=None (backward compatible)."""
    tool = BashTool()
    assert tool.shared_dir is None


async def test_bashtool_executes_without_shared_dir(tmp_path: Path) -> None:
    """BashTool without shared_dir still executes commands normally."""
    tool = BashTool()
    result = await tool.execute({"command": "echo hello"}, tmp_path)
    assert not result.is_error
    assert "hello" in result.content


# ---------------------------------------------------------------------------
# Base64 encoding tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _IS_LINUX, reason="Linux sandbox only")
def test_base64_in_sandboxed_command(tmp_path: Path) -> None:
    """Sandboxed command uses base64 encoding instead of manual escaping."""
    if not sandbox_available():
        pytest.skip("Linux sandbox only")
    cmd, _ = build_sandboxed_command("echo hi", tmp_path)
    assert "base64 -d" in cmd
    # Should NOT contain the old-style escaped inner script in double quotes
    # (the user command should be base64-encoded, not directly embedded)
    assert 'echo hi' not in cmd  # raw command should be encoded, not visible


@pytest.mark.skipif(not _IS_LINUX, reason="Linux sandbox only")
def test_special_chars_preserved_in_base64(tmp_path: Path) -> None:
    """Commands with $, single quotes, backticks survive base64 encoding."""
    if not sandbox_available():
        pytest.skip("Linux sandbox only")
    tricky_cmd = "echo \"hello world\" | awk '{print $2}'"
    cmd, _ = build_sandboxed_command(tricky_cmd, tmp_path)
    # Extract the base64 string from the command and decode it
    # Format: ... eval $(echo <BASE64> | base64 -d)
    import re
    match = re.search(r'echo ([A-Za-z0-9+/=]+) \| base64 -d', cmd)
    assert match, f"Could not find base64 payload in: {cmd[:200]}"
    decoded = base64.b64decode(match.group(1)).decode()
    # The decoded script should contain the original command intact
    assert tricky_cmd in decoded


# ---------------------------------------------------------------------------
# pivot_root isolation tests
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _IS_LINUX, reason="Linux sandbox only")
def test_pivot_root_structure(tmp_path: Path) -> None:
    """Sandboxed command uses pivot_root '.' '.' trick (no .pivot_old)."""
    if not sandbox_available():
        pytest.skip("Linux sandbox only")
    cmd, _ = build_sandboxed_command("echo hi", tmp_path)
    # Must use pivot_root, not chroot
    assert "pivot_root" in cmd
    assert "chroot" not in cmd
    # Must use "pivot_root . ." (dot-dot trick), NOT "pivot_root . .pivot_old"
    assert "pivot_root . ." in cmd
    assert ".pivot_old" not in cmd
    # Must lazy-detach stacked old root
    assert "umount -l . 2>/dev/null" in cmd
    # Must make mount propagation private
    assert "mount --make-rprivate /" in cmd
    # Must self-bind CWD for pivot_root
    cwd_abs = str(tmp_path.resolve())
    assert f"mount --bind {cwd_abs} {cwd_abs}" in cmd


@pytest.mark.skipif(not _IS_LINUX, reason="Linux sandbox only")
def test_proc_mounted_after_pivot(tmp_path: Path) -> None:
    """Fresh /proc is mounted after pivot_root, not before."""
    if not sandbox_available():
        pytest.skip("Linux sandbox only")
    cmd, _ = build_sandboxed_command("echo hi", tmp_path)
    # /proc mount must come AFTER pivot_root (for PID namespace isolation)
    pivot_pos = cmd.index("pivot_root")
    proc_mount_pos = cmd.index("mount -t proc proc /proc")
    assert proc_mount_pos > pivot_pos
