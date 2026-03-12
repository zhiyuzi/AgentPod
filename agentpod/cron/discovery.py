"""Cron task discovery: scan a cron directory for TASK.md definitions."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Dict, List

from croniter import croniter

from agentpod.skills import load_frontmatter_and_body

_log = logging.getLogger("agentpod.cron")

_REQUIRED_FIELDS = ("name", "description", "schedule")

_DEFAULTS: Dict[str, Any] = {
    "timezone": "Asia/Shanghai",
    "enabled": True,
    "timeout": 1200,
    "max_turns": 0,
    "model": "",
}


def discover_cron_tasks(cron_dir: Path, min_interval: int = 0) -> List[Dict[str, Any]]:
    """Scan *cron_dir* for subdirectories containing ``TASK.md``.

    Returns a sorted list of validated cron-task dicts.  Invalid entries
    are logged as warnings and silently skipped.
    """
    cron_dir = Path(cron_dir)
    if not cron_dir.is_dir():
        return []

    results: List[Dict[str, Any]] = []

    for child in sorted(cron_dir.iterdir()):
        if not child.is_dir():
            continue
        task_md = child / "TASK.md"
        if not task_md.is_file():
            continue

        meta, body = load_frontmatter_and_body(task_md)
        dir_name = child.name

        # --- required fields ---
        missing = [f for f in _REQUIRED_FIELDS if not meta.get(f)]
        if missing:
            _log.warning(
                "Cron task '%s': missing required field(s) %s in frontmatter, skipped",
                dir_name,
                ", ".join(missing),
            )
            continue

        name = str(meta["name"])
        if name != dir_name:
            _log.warning(
                "Cron task '%s': frontmatter name '%s' does not match directory name, skipped",
                dir_name,
                name,
            )
            continue

        schedule = str(meta["schedule"])
        if not croniter.is_valid(schedule):
            _log.warning(
                "Cron task '%s': invalid cron expression '%s', skipped",
                dir_name,
                schedule,
            )
            continue

        if min_interval > 0:
            from agentpod.cron.writer import compute_min_interval

            interval = compute_min_interval(schedule)
            if interval < min_interval:
                _log.warning(
                    "Cron task '%s': schedule interval (%ds) below minimum (%ds), skipped",
                    dir_name,
                    int(interval),
                    min_interval,
                )
                continue

        description = str(meta["description"])

        prompt = body.strip()
        raw_content = task_md.read_text(encoding="utf-8")
        content_hash = hashlib.md5(raw_content.encode()).hexdigest()

        entry: Dict[str, Any] = {
            "name": name,
            "description": description,
            "schedule": schedule,
            "timezone": str(meta.get("timezone", _DEFAULTS["timezone"])),
            "enabled": meta.get("enabled", _DEFAULTS["enabled"]),
            "timeout": int(meta.get("timeout", _DEFAULTS["timeout"])),
            "max_turns": int(meta.get("max_turns", _DEFAULTS["max_turns"])),
            "model": str(meta.get("model", _DEFAULTS["model"])),
            "prompt": prompt,
            "content_hash": content_hash,
            "dir": child,
        }
        results.append(entry)

    return results
