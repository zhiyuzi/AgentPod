"""Tests for shared layer bind-mount integration in sandbox/isolate.py and BashTool."""

from __future__ import annotations

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
    # No shared bind-mount lines should appear
    assert "shared" not in cmd
    # The basic chroot structure must still be present
    assert "chroot" in cmd
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
