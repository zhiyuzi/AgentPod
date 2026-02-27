"""OS-level sandbox for BashTool command execution.

Design reference: .docs/spec-v1.0/design.md §11.2

Isolation layers (Linux only):
  1. User namespace (unshare -r)  — gain CAP_SYS_CHROOT without real root
  2. Mount namespace (unshare -m) — private mount tree, changes don't leak
  3. PID namespace (unshare -p)   — process isolation, can't see/signal host PIDs
  4. chroot to CWD                — filesystem boundary, can't see outside CWD
  5. /proc mount inside chroot    — basic commands (ps, etc.) work within sandbox
  6. Drop to nobody (optional)    — even inside chroot, not uid=0

Non-Linux: falls back to plain subprocess with cwd= (no isolation).
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
from pathlib import Path

# Binaries needed for sandbox — resolved once at import time
_IS_LINUX = platform.system() == "Linux"
_UNSHARE = shutil.which("unshare") if _IS_LINUX else None
_CHROOT_AVAILABLE = _IS_LINUX  # chroot is done via unshare --root


def sandbox_available() -> bool:
    """Return True if OS-level sandbox can be used."""
    return _IS_LINUX and _UNSHARE is not None


def _needs_proc(cwd: Path) -> bool:
    """Check if we need to mount /proc inside the chroot."""
    return not (cwd / "proc").exists()


def _prepare_sandbox_dirs(cwd: Path) -> list[Path]:
    """Create minimal directory structure needed inside chroot.

    Returns list of directories created (for cleanup).
    """
    created: list[Path] = []

    # /tmp — many commands expect it
    tmp_dir = cwd / "tmp"
    if not tmp_dir.exists():
        tmp_dir.mkdir(exist_ok=True)
        created.append(tmp_dir)

    # /dev/null — needed by shell redirections
    dev_dir = cwd / "dev"
    if not dev_dir.exists():
        dev_dir.mkdir(exist_ok=True)
        created.append(dev_dir)

    return created


def build_sandboxed_command(command: str, cwd: Path) -> tuple[str, Path | None]:
    """Wrap a shell command with sandbox isolation.

    Returns:
        (wrapped_command, effective_cwd)
        - On Linux: command wrapped with unshare+chroot, cwd=None (chroot handles it)
        - On non-Linux: original command, cwd=cwd (fallback)
    """
    if not sandbox_available():
        return command, cwd

    # Build the unshare command:
    #   unshare --user --map-root-user --mount --pid --fork --root=<cwd> \
    #     /bin/sh -c '<mount proc if needed>; <actual command>'
    #
    # --user --map-root-user: create user namespace, map current uid to root inside
    #   (needed for chroot permission, but we're NOT real root)
    # --mount: private mount namespace (proc mount doesn't affect host)
    # --pid --fork: PID namespace (process isolation)
    # --root=<cwd>: chroot into CWD

    cwd_abs = str(cwd.resolve())

    # Inner script: mount /proc if available, then exec the user command
    # We use /bin/sh -c because the chroot may not have bash
    inner_parts = []

    # Mount /proc for PID namespace to work properly
    inner_parts.append("mount -t proc proc /proc 2>/dev/null")

    # Set HOME to / (inside chroot, CWD is /)
    inner_parts.append("export HOME=/")

    # Clean environment: remove sensitive vars
    sensitive_vars = [
        "VOLCENGINE_API_KEY", "ANTHROPIC_API_KEY", "ZHIPU_API_KEY",
        "MINIMAX_API_KEY", "AGENTPOD_DATA_DIR",
    ]
    for var in sensitive_vars:
        inner_parts.append(f"unset {var}")

    # The actual user command
    inner_parts.append(command)

    inner_script = "; ".join(inner_parts)

    # Escape single quotes in the inner script for shell wrapping
    escaped_inner = inner_script.replace("'", "'\\''")

    wrapped = (
        f"{_UNSHARE} --user --map-root-user --mount --pid --fork "
        f"--root={cwd_abs} "
        f"/bin/sh -c '{escaped_inner}'"
    )

    # effective_cwd is None because chroot sets / = cwd
    return wrapped, None


async def run_sandboxed(
    command: str,
    cwd: Path,
    timeout: int = 120,
) -> tuple[str, int]:
    """Execute a command inside the sandbox.

    Returns:
        (output, return_code)
    """
    wrapped_cmd, effective_cwd = build_sandboxed_command(command, cwd)

    cwd_str = str(effective_cwd) if effective_cwd else None

    proc = await asyncio.create_subprocess_shell(
        wrapped_cmd,
        cwd=cwd_str,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        # Clean env for sandboxed execution on Linux
        env=_build_sandbox_env() if sandbox_available() else None,
    )

    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace") if stdout else ""
        return output, proc.returncode or 0
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return f"Command timed out after {timeout} seconds", -1


def _build_sandbox_env() -> dict[str, str]:
    """Build a minimal, sanitized environment for sandboxed commands."""
    # Start with a minimal set — don't inherit the full host env
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/",
        "TERM": os.environ.get("TERM", "xterm"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
    }
    return env
