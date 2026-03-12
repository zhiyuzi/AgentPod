"""CWD → DB synchronization for cron tasks."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from croniter import croniter

from agentpod.cron.discovery import discover_cron_tasks
from agentpod.db import Database

_log = logging.getLogger("agentpod.cron")


def compute_next_run(schedule: str, tz_name: str) -> str:
    """Compute the next run time as UTC ISO string."""
    tz = ZoneInfo(tz_name)
    now_local = datetime.now(tz)
    cron = croniter(schedule, now_local)
    next_local = cron.get_next(datetime)
    next_utc = next_local.astimezone(timezone.utc)
    return next_utc.isoformat()


class CronSyncManager:
    """Synchronizes cron task definitions from CWD files to the database."""

    def __init__(self, db: Database, min_interval: int = 0):
        self.db = db
        self.min_interval = min_interval

    def sync_user(self, user_id: str, cwd_path: str) -> dict:
        """Sync a single user's cron tasks from their CWD to DB.

        Returns a summary dict: {"created": int, "updated": int, "deleted": int, "unchanged": int}
        """
        cron_dir = Path(cwd_path) / ".agents" / "cron"
        disk_tasks = discover_cron_tasks(cron_dir, min_interval=self.min_interval)

        # Build lookup: name -> disk task
        disk_map = {t["name"]: t for t in disk_tasks}

        # Get existing DB tasks for this user (including deleted for restore)
        db_tasks = self.db.list_cron_tasks(user_id, include_deleted=True)
        db_map = {t["task_name"]: t for t in db_tasks}

        created = 0
        updated = 0
        deleted = 0
        unchanged = 0

        # Process tasks on disk
        for name, task in disk_map.items():
            task_id = f"{user_id}:{name}"
            next_run = compute_next_run(task["schedule"], task["timezone"])

            existing = db_map.get(name)
            if existing is None:
                # New task → INSERT
                self.db.upsert_cron_task(
                    task_id=task_id, user_id=user_id, task_name=name,
                    description=task["description"], schedule=task["schedule"],
                    timezone=task["timezone"], enabled=task["enabled"],
                    timeout=task["timeout"], max_turns=task["max_turns"],
                    model=task["model"], content_hash=task["content_hash"],
                    next_run_at=next_run,
                )
                created += 1
                _log.info("Cron task '%s' created for user '%s'", name, user_id)
            elif existing["deleted"]:
                # Was soft-deleted, now back on disk → restore
                self.db.upsert_cron_task(
                    task_id=task_id, user_id=user_id, task_name=name,
                    description=task["description"], schedule=task["schedule"],
                    timezone=task["timezone"], enabled=task["enabled"],
                    timeout=task["timeout"], max_turns=task["max_turns"],
                    model=task["model"], content_hash=task["content_hash"],
                    next_run_at=next_run,
                )
                created += 1
                _log.info("Cron task '%s' restored for user '%s'", name, user_id)
            elif existing["content_hash"] != task["content_hash"]:
                # Content changed → UPDATE
                self.db.upsert_cron_task(
                    task_id=task_id, user_id=user_id, task_name=name,
                    description=task["description"], schedule=task["schedule"],
                    timezone=task["timezone"], enabled=task["enabled"],
                    timeout=task["timeout"], max_turns=task["max_turns"],
                    model=task["model"], content_hash=task["content_hash"],
                    next_run_at=next_run,
                )
                updated += 1
                _log.info("Cron task '%s' updated for user '%s'", name, user_id)
            else:
                unchanged += 1

        # Tasks in DB but not on disk → soft delete
        for name, db_task in db_map.items():
            if name not in disk_map and not db_task["deleted"]:
                self.db.soft_delete_cron_task(db_task["id"])
                deleted += 1
                _log.info("Cron task '%s' soft-deleted for user '%s'", name, user_id)

        return {"created": created, "updated": updated, "deleted": deleted, "unchanged": unchanged}

    def sync_all_users(self) -> dict:
        """Sync cron tasks for all users. Returns {user_id: summary}."""
        users = self.db.list_users()
        results = {}
        for user in users:
            user_id = user["id"]
            cwd_path = user["cwd_path"]
            results[user_id] = self.sync_user(user_id, cwd_path)
        return results
