"""Cron task write operations: validate, generate TASK.md, write/update/delete on disk."""

from __future__ import annotations

import re
import shutil
import logging
from datetime import datetime
from pathlib import Path

from croniter import croniter

from agentpod.skills import load_frontmatter_and_body

_log = logging.getLogger("agentpod.cron")

_NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")
_NAME_MAX_LEN = 64


def compute_min_interval(schedule: str) -> float:
    """Compute the minimum trigger interval (seconds) for a cron expression."""
    now = datetime.now()
    cron = croniter(schedule, now)
    first = cron.get_next(datetime)
    second = cron.get_next(datetime)
    return (second - first).total_seconds()


def validate_task_name(name: str) -> None:
    """Validate task name: lowercase alphanumeric + hyphens, max 64 chars."""
    if not name:
        raise ValueError("Task name cannot be empty")
    if len(name) > _NAME_MAX_LEN:
        raise ValueError(f"Task name too long (max {_NAME_MAX_LEN} chars): {name}")
    if not _NAME_RE.match(name):
        raise ValueError(
            f"Invalid task name '{name}': must match [a-z0-9] with hyphens, "
            "no leading/trailing hyphens"
        )


def validate_schedule(schedule: str, min_interval: int = 0) -> None:
    """Validate cron expression syntax and minimum interval."""
    if not croniter.is_valid(schedule):
        raise ValueError(f"Invalid cron expression: {schedule}")
    if min_interval > 0:
        interval = compute_min_interval(schedule)
        if interval < min_interval:
            raise ValueError(
                f"Schedule interval ({int(interval)}s) is below minimum "
                f"({min_interval}s / {min_interval // 3600}h)"
            )


def generate_task_md(
    *,
    name: str,
    description: str,
    schedule: str,
    prompt: str,
    timezone: str = "Asia/Shanghai",
    enabled: bool = True,
    timeout: int = 1200,
    max_turns: int = 0,
    model: str = "",
) -> str:
    """Generate TASK.md content (YAML frontmatter + body)."""
    lines = ["---"]
    lines.append(f"name: {name}")
    lines.append(f"description: {description}")
    lines.append(f'schedule: "{schedule}"')
    if timezone != "Asia/Shanghai":
        lines.append(f"timezone: {timezone}")
    if not enabled:
        lines.append("enabled: false")
    if timeout != 1200:
        lines.append(f"timeout: {timeout}")
    if max_turns:
        lines.append(f"max_turns: {max_turns}")
    if model:
        lines.append(f"model: {model}")
    lines.append("---")
    lines.append("")
    lines.append(prompt)
    lines.append("")
    return "\n".join(lines)


def create_cron_task(
    cwd_path: str,
    *,
    name: str,
    description: str,
    schedule: str,
    prompt: str,
    timezone: str = "Asia/Shanghai",
    enabled: bool = True,
    timeout: int = 1200,
    max_turns: int = 0,
    model: str = "",
    min_interval: int = 0,
) -> None:
    """Create a new cron task: validate -> mkdir -> write TASK.md."""
    validate_task_name(name)
    validate_schedule(schedule, min_interval)

    task_dir = Path(cwd_path) / ".agents" / "cron" / name
    task_md = task_dir / "TASK.md"
    if task_md.is_file():
        raise FileExistsError(f"Task already exists: {name}")

    content = generate_task_md(
        name=name, description=description, schedule=schedule, prompt=prompt,
        timezone=timezone, enabled=enabled, timeout=timeout,
        max_turns=max_turns, model=model,
    )
    task_dir.mkdir(parents=True, exist_ok=True)
    task_md.write_text(content, encoding="utf-8")
    _log.info("Cron task '%s' created on disk at %s", name, task_md)


def update_cron_task(
    cwd_path: str,
    name: str,
    *,
    description: str | None = None,
    schedule: str | None = None,
    prompt: str | None = None,
    timezone: str | None = None,
    enabled: bool | None = None,
    timeout: int | None = None,
    max_turns: int | None = None,
    model: str | None = None,
    min_interval: int = 0,
) -> None:
    """Update an existing cron task: read -> merge -> validate -> overwrite."""
    task_dir = Path(cwd_path) / ".agents" / "cron" / name
    task_md = task_dir / "TASK.md"
    if not task_md.is_file():
        raise FileNotFoundError(f"Task not found: {name}")

    meta, body = load_frontmatter_and_body(task_md)

    # Merge: only override non-None fields
    merged = {
        "name": name,
        "description": description if description is not None else str(meta.get("description", "")),
        "schedule": schedule if schedule is not None else str(meta.get("schedule", "")),
        "prompt": prompt if prompt is not None else body.strip(),
        "timezone": timezone if timezone is not None else str(meta.get("timezone", "Asia/Shanghai")),
        "enabled": enabled if enabled is not None else meta.get("enabled", True),
        "timeout": timeout if timeout is not None else int(meta.get("timeout", 1200)),
        "max_turns": max_turns if max_turns is not None else int(meta.get("max_turns", 0)),
        "model": model if model is not None else str(meta.get("model", "")),
    }

    # Validate schedule (always, in case it changed or min_interval changed)
    validate_schedule(merged["schedule"], min_interval)

    content = generate_task_md(
        name=merged["name"], description=merged["description"],
        schedule=merged["schedule"], prompt=merged["prompt"],
        timezone=merged["timezone"], enabled=merged["enabled"],
        timeout=merged["timeout"], max_turns=merged["max_turns"],
        model=merged["model"],
    )
    task_md.write_text(content, encoding="utf-8")
    _log.info("Cron task '%s' updated on disk at %s", name, task_md)


def delete_cron_task_files(cwd_path: str, name: str) -> None:
    """Delete the task directory (.agents/cron/{name}/) from disk."""
    task_dir = Path(cwd_path) / ".agents" / "cron" / name
    if not task_dir.is_dir():
        raise FileNotFoundError(f"Task directory not found: {name}")
    shutil.rmtree(task_dir)
    _log.info("Cron task '%s' deleted from disk at %s", name, task_dir)
