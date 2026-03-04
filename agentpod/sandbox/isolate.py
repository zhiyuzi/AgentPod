"""OS-level sandbox for BashTool command execution.

Design reference: .docs/spec-v1.0/design.md §11.2

Isolation layers (Linux only):
  1. User namespace (unshare -r)  — gain capabilities without real root
  2. Mount namespace (unshare -m) — private mount tree, changes don't leak
  3. PID namespace (unshare -p)   — process isolation, can't see/signal host PIDs
  4. Network namespace (unshare -n)— no network interfaces, no outbound access
  5. Bind-mount /bin /usr /lib etc read-only+nosuid into CWD — commands available
  6. pivot_root to CWD + unmount old root — filesystem boundary, fd escape impossible
  7. Environment sanitization     — no API keys leak into sandbox

Non-Linux: falls back to plain subprocess with cwd= (no isolation).
"""

from __future__ import annotations

import asyncio
import base64
import os
import platform
import shutil
from pathlib import Path

_IS_LINUX = platform.system() == "Linux"
_UNSHARE = shutil.which("unshare") if _IS_LINUX else None
_PIVOT_ROOT = shutil.which("pivot_root") if _IS_LINUX else None
_SYSTEMD_RUN = shutil.which("systemd-run") if _IS_LINUX else None

# System directories to bind-mount read-only into the new root.
# These provide shell, coreutils, libraries, and basic device nodes.
# /etc/alternatives is needed for update-alternatives symlinks (awk, vim, etc.)
# Note: /proc is NOT in this list — it's mounted fresh after pivot_root
# to ensure PID namespace isolation (only sandbox PIDs visible).
_BIND_MOUNT_DIRS = ["/bin", "/usr", "/lib", "/lib64", "/etc/alternatives", "/dev"]

# Paths excluded from shared layer bind-mounts (relative to shared_dir root).
_SHARED_EXCLUDE = {".agents/cron", "sessions", "version"}


def sandbox_available() -> bool:
    """Return True if OS-level sandbox can be used."""
    return _IS_LINUX and _UNSHARE is not None and _PIVOT_ROOT is not None


def _build_mount_script(cwd_abs: str) -> str:
    """Build shell commands to set up bind mounts before pivot_root.

    Strategy:
      1. Make mount propagation private (prevent leaking to host)
      2. Bind-mount CWD onto itself (required for pivot_root in user ns —
         inherited mounts are MNT_LOCKED, self-bind creates an unlocked mount)
      3. Create mount-point directories inside CWD
      4. Bind-mount host dirs read-only
      5. Create /tmp inside CWD
    """
    lines = [
        # Private mount propagation — prevent mount events from leaking to host
        "mount --make-rprivate /",
        # Self-bind CWD — required for pivot_root in user namespace
        f"mount --bind {cwd_abs} {cwd_abs}",
    ]

    # Create mount points and bind-mount
    for d in _BIND_MOUNT_DIRS:
        target = f"{cwd_abs}{d}"
        if d == "/dev":
            # /dev: recursive bind-mount for /dev/null, /dev/urandom etc.
            lines.append(f"mkdir -p {target}")
            lines.append(f"mount --rbind /dev {target} 2>/dev/null")
        else:
            # Regular dirs: bind-mount read-only
            lines.append(f"mkdir -p {target}")
            lines.append(f"mount --bind {d} {target} 2>/dev/null")
            lines.append(f"mount -o remount,ro,nosuid,bind {target} 2>/dev/null")

    # Create /tmp inside CWD
    lines.append(f"mkdir -p {cwd_abs}/tmp")

    return "; ".join(lines)


def build_sandboxed_command(
    command: str,
    cwd: Path,
    shared_dir: Path | None = None,
    memory_max: str = "",
    cpu_quota: str = "",
    pids_max: str = "",
) -> tuple[str, Path | None]:
    """Wrap a shell command with sandbox isolation.

    Returns:
        (wrapped_command, effective_cwd)
        - On Linux: command wrapped with unshare + bind-mount + pivot_root
        - On non-Linux: original command, cwd=cwd (fallback)
    """
    if not sandbox_available():
        return command, cwd

    cwd_abs = str(cwd.resolve())

    # Flow:
    #   unshare --user --map-root-user --mount --pid --net --fork /bin/sh -c '
    #     mount --make-rprivate /
    #     mount --bind CWD CWD          (make CWD a mount point for pivot_root)
    #     <bind mount system dirs>
    #     <shared layer mounts>
    #     cd CWD; pivot_root . .pivot_old
    #     mount -t tmpfs -o ... tmpfs /.pivot_old  (hide old root)
    #     mount -t proc proc /proc      (fresh procfs for PID namespace)
    #     eval $(echo <BASE64> | base64 -d)
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

    # Inner script (runs after pivot_root, inside the new root)
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

    # Encode inner script as base64 to avoid shell quoting issues.
    # Base64 bypasses all shell interpretation layers.
    encoded_inner = base64.b64encode(inner_script.encode()).decode()

    shared_mount_script = "; ".join(shared_mount_lines) if shared_mount_lines else ""

    # pivot_root . .pivot_old + tmpfs overmount:
    # umount /.pivot_old fails in user namespaces (MNT_LOCKED on inherited mounts).
    # "pivot_root . ." trick also failed in practice (umount -l . detached the
    # new root instead of the old root, leaving process on host filesystem).
    # Solution: keep .pivot_old but overmount it with an empty, inaccessible tmpfs.
    # The old root is hidden underneath and unreachable via normal path traversal.
    pivot_parts = [
        f"cd {cwd_abs}",
        "mkdir -p .pivot_old",
        f"{_PIVOT_ROOT} . .pivot_old",
        # Hide old root: overmount with empty, read-only, mode=000 tmpfs.
        # Even with CAP_SYS_ADMIN in user ns, accessing mode=000 dir fails.
        "mount -t tmpfs -o size=0,nr_inodes=1,mode=000 tmpfs /.pivot_old 2>/dev/null",
        # Fresh /proc for PID namespace — mounted AFTER pivot so it only
        # shows sandbox PIDs, not host PIDs
        "mkdir -p /proc",
        "mount -t proc proc /proc 2>/dev/null",
    ]
    pivot_script = "; ".join(pivot_parts)

    # Assemble outer script: mounts → shared mounts → pivot → user command
    parts = [mount_script]
    if shared_mount_script:
        parts.append(shared_mount_script)
    parts.append(pivot_script)
    parts.append(f"eval $(echo {encoded_inner} | base64 -d)")
    outer_script = "; ".join(parts)

    escaped_outer = outer_script.replace("'", "'\\''")

    wrapped = (
        f"{_UNSHARE} --user --map-root-user --mount --pid --net --fork "
        f"/bin/sh -c '{escaped_outer}'"
    )

    # cgroups resource limits via systemd-run
    if _SYSTEMD_RUN and any([memory_max, cpu_quota, pids_max]):
        props = []
        if memory_max:
            props.append(f"-p MemoryMax={memory_max}")
        if cpu_quota:
            props.append(f"-p CPUQuota={cpu_quota}")
        if pids_max:
            props.append(f"-p TasksMax={pids_max}")
        props_str = " ".join(props)
        wrapped = f"{_SYSTEMD_RUN} --user --scope -q {props_str} -- {wrapped}"

    return wrapped, None


async def run_sandboxed(
    command: str,
    cwd: Path,
    timeout: int = 120,
    shared_dir: Path | None = None,
    memory_max: str = "",
    cpu_quota: str = "",
    pids_max: str = "",
) -> tuple[str, int]:
    """Execute a command inside the sandbox.

    Returns:
        (output, return_code)
    """
    wrapped_cmd, effective_cwd = build_sandboxed_command(
        command, cwd, shared_dir=shared_dir,
        memory_max=memory_max, cpu_quota=cpu_quota, pids_max=pids_max,
    )

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
