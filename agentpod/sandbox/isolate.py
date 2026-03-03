"""OS-level sandbox for BashTool command execution.

Design reference: .docs/spec-v1.0/design.md §11.2

Isolation layers (Linux only):
  1. User namespace (unshare -r)  — gain CAP_SYS_CHROOT without real root
  2. Mount namespace (unshare -m) — private mount tree, changes don't leak
  3. PID namespace (unshare -p)   — process isolation, can't see/signal host PIDs
  4. Network namespace (unshare -n)— no network interfaces, no outbound access
  5. Bind-mount /bin /usr /lib etc read-only+nosuid into CWD — commands available
  6. chroot to CWD                — filesystem boundary, can't see outside CWD
  7. Environment sanitization     — no API keys leak into sandbox

Non-Linux: falls back to plain subprocess with cwd= (no isolation).
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
from pathlib import Path

_IS_LINUX = platform.system() == "Linux"
_UNSHARE = shutil.which("unshare") if _IS_LINUX else None
_CHROOT = shutil.which("chroot") if _IS_LINUX else None

# System directories to bind-mount read-only into the chroot.
# These provide shell, coreutils, libraries, and basic device nodes.
# /etc/alternatives is needed for update-alternatives symlinks (awk, vim, etc.)
_BIND_MOUNT_DIRS = ["/bin", "/usr", "/lib", "/lib64", "/etc/alternatives", "/dev", "/proc"]

# Paths excluded from shared layer bind-mounts (relative to shared_dir root).
_SHARED_EXCLUDE = {".agents/cron", "sessions", "version"}


def sandbox_available() -> bool:
    """Return True if OS-level sandbox can be used."""
    return _IS_LINUX and _UNSHARE is not None and _CHROOT is not None


def _build_mount_script(cwd_abs: str) -> str:
    """Build shell commands to set up bind mounts inside the chroot.

    Strategy:
      1. Create mount-point directories inside CWD
      2. Bind-mount host dirs read-only
      3. Mount /proc (for PID namespace)
      4. Create /tmp inside chroot
      5. chroot into CWD
      6. cd / (now inside chroot, / = CWD)
    """
    lines = []

    # Create mount points and bind-mount
    for d in _BIND_MOUNT_DIRS:
        target = f"{cwd_abs}{d}"
        if d == "/proc":
            # /proc is special — mount a new proc filesystem, not bind-mount
            lines.append(f"mkdir -p {target}")
            lines.append(f"mount -t proc proc {target} 2>/dev/null")
        elif d == "/dev":
            # /dev: bind-mount for /dev/null, /dev/urandom etc.
            lines.append(f"mkdir -p {target}")
            lines.append(f"mount --rbind /dev {target} 2>/dev/null")
        else:
            # Regular dirs: bind-mount read-only
            lines.append(f"mkdir -p {target}")
            lines.append(f"mount --bind {d} {target} 2>/dev/null")
            lines.append(f"mount -o remount,ro,nosuid,bind {target} 2>/dev/null")

    # Create /tmp inside chroot
    lines.append(f"mkdir -p {cwd_abs}/tmp")

    return "; ".join(lines)


def build_sandboxed_command(
    command: str,
    cwd: Path,
    shared_dir: Path | None = None,
) -> tuple[str, Path | None]:
    """Wrap a shell command with sandbox isolation.

    Returns:
        (wrapped_command, effective_cwd)
        - On Linux: command wrapped with unshare + bind-mount + chroot
        - On non-Linux: original command, cwd=cwd (fallback)
    """
    if not sandbox_available():
        return command, cwd

    cwd_abs = str(cwd.resolve())

    # The outer unshare creates user + mount + PID namespaces.
    # Inside, we set up bind mounts, then chroot + exec the user command.
    #
    # Flow:
    #   unshare -r -m -p -f /bin/sh -c '
    #     <bind mount setup>
    #     chroot <cwd> /bin/sh -c '
    #       <env cleanup>
    #       cd /
    #       <user command>
    #     '
    #   '

    mount_script = _build_mount_script(cwd_abs)

    # Build shared layer bind-mount commands if shared_dir is provided
    shared_mount_lines = []
    if shared_dir and shared_dir.is_dir():
        for item in sorted(shared_dir.iterdir()):
            rel = item.name
            if rel in _SHARED_EXCLUDE:
                continue
            if rel == ".agents" and item.is_dir():
                for sub in sorted(item.iterdir()):
                    sub_rel = f".agents/{sub.name}"
                    if sub_rel in _SHARED_EXCLUDE:
                        continue
                    if sub.name == "skills" and sub.is_dir():
                        # skills: mount per skill-dir, user same-name takes priority
                        for skill_dir in sorted(sub.iterdir()):
                            if not skill_dir.is_dir():
                                continue
                            user_skill = Path(cwd_abs) / ".agents" / "skills" / skill_dir.name
                            if user_skill.is_dir():
                                continue  # user has same-named skill, skip
                            target = f"{cwd_abs}/.agents/skills/{skill_dir.name}"
                            shared_mount_lines.append(f"mkdir -p {target}")
                            shared_mount_lines.append(f"mount --bind {skill_dir} {target} 2>/dev/null")
                            shared_mount_lines.append(f"mount -o remount,ro,nosuid,bind {target} 2>/dev/null")
                    else:
                        # Other .agents subdirs: mount whole dir if user doesn't have it
                        user_sub = Path(cwd_abs) / sub_rel
                        if user_sub.exists():
                            continue
                        target = f"{cwd_abs}/{sub_rel}"
                        shared_mount_lines.append(f"mkdir -p {target}")
                        shared_mount_lines.append(f"mount --bind {sub} {target} 2>/dev/null")
                        shared_mount_lines.append(f"mount -o remount,ro,nosuid,bind {target} 2>/dev/null")
                continue
            # Top-level file/dir: skip if user already has it
            user_item = Path(cwd_abs) / rel
            if user_item.exists():
                continue
            target = f"{cwd_abs}/{rel}"
            if item.is_dir():
                shared_mount_lines.append(f"mkdir -p {target}")
            else:
                shared_mount_lines.append(f"mkdir -p $(dirname {target}) && touch {target}")
            shared_mount_lines.append(f"mount --bind {item} {target} 2>/dev/null")
            shared_mount_lines.append(f"mount -o remount,ro,nosuid,bind {target} 2>/dev/null")

    # Inner script (runs inside chroot)
    inner_parts = [
        "cd /",
        "export HOME=/",
        "export PATH=/usr/local/bin:/usr/bin:/bin",
    ]

    # Clean sensitive env vars
    for var in [
        "VOLCENGINE_API_KEY", "ANTHROPIC_API_KEY", "ZHIPU_API_KEY",
        "MINIMAX_API_KEY", "AGENTPOD_DATA_DIR",
    ]:
        inner_parts.append(f"unset {var}")

    inner_parts.append(command)
    inner_script = "; ".join(inner_parts)

    # Escape for nested shell quoting:
    # outer: single quotes around the whole unshare script
    # inner: the chroot command uses double quotes
    escaped_inner = inner_script.replace("\\", "\\\\").replace('"', '\\"')

    shared_mount_script = "; ".join(shared_mount_lines) if shared_mount_lines else ""
    if shared_mount_script:
        outer_script = f'{mount_script}; {shared_mount_script}; {_CHROOT} {cwd_abs} /bin/sh -c "{escaped_inner}"'
    else:
        outer_script = f'{mount_script}; {_CHROOT} {cwd_abs} /bin/sh -c "{escaped_inner}"'
    escaped_outer = outer_script.replace("'", "'\\''")

    wrapped = (
        f"{_UNSHARE} --user --map-root-user --mount --pid --net --fork "
        f"/bin/sh -c '{escaped_outer}'"
    )

    return wrapped, None


async def run_sandboxed(
    command: str,
    cwd: Path,
    timeout: int = 120,
    shared_dir: Path | None = None,
) -> tuple[str, int]:
    """Execute a command inside the sandbox.

    Returns:
        (output, return_code)
    """
    wrapped_cmd, effective_cwd = build_sandboxed_command(command, cwd, shared_dir=shared_dir)

    cwd_str = str(effective_cwd) if effective_cwd else None

    proc = await asyncio.create_subprocess_shell(
        wrapped_cmd,
        cwd=cwd_str,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
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
    except asyncio.CancelledError:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        raise


def _build_sandbox_env() -> dict[str, str]:
    """Build a minimal, sanitized environment for sandboxed commands."""
    return {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": "/",
        "TERM": os.environ.get("TERM", "xterm"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
    }
