"""Tests for agentpod.cron.sync."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from agentpod.cron.sync import CronSyncManager, compute_next_run
from agentpod.db import Database


@pytest.fixture()
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    database = Database(db_path)
    database.init_db()
    yield database
    database.close()


def _make_task(cron_dir: Path, name: str, schedule: str = "0 9 * * *",
               description: str = "test task", body: str = "do something"):
    task_dir = cron_dir / name
    task_dir.mkdir(parents=True, exist_ok=True)
    content = f"""---
name: {name}
description: {description}
schedule: "{schedule}"
---

{body}
"""
    (task_dir / "TASK.md").write_text(content, encoding="utf-8")


class TestComputeNextRun:
    def test_returns_valid_utc_iso(self):
        result = compute_next_run("0 9 * * *", "Asia/Shanghai")
        # Should be a valid ISO datetime string ending with +00:00 (UTC)
        dt = datetime.fromisoformat(result)
        assert dt.tzinfo is not None
        assert dt.utcoffset().total_seconds() == 0
        # Should be in the future
        assert dt > datetime.now(timezone.utc)

    def test_different_timezone(self):
        result = compute_next_run("0 9 * * *", "UTC")
        dt = datetime.fromisoformat(result)
        assert dt.tzinfo is not None
        assert dt.utcoffset().total_seconds() == 0


class TestSyncNewTask:
    def test_sync_new_task(self, db: Database, tmp_path: Path):
        cwd = tmp_path / "cwd"
        cron_dir = cwd / ".agents" / "cron"
        _make_task(cron_dir, "daily-report")
        db.create_user("alice", str(cwd))

        mgr = CronSyncManager(db)
        result = mgr.sync_user("alice", str(cwd))

        assert result["created"] == 1
        assert result["updated"] == 0
        assert result["deleted"] == 0
        assert result["unchanged"] == 0

        # Verify task exists in DB
        task = db.get_cron_task("alice:daily-report")
        assert task is not None
        assert task["task_name"] == "daily-report"
        assert task["schedule"] == "0 9 * * *"
        assert task["deleted"] == 0

class TestSyncUnchanged:
    def test_sync_unchanged(self, db: Database, tmp_path: Path):
        cwd = tmp_path / "cwd"
        cron_dir = cwd / ".agents" / "cron"
        _make_task(cron_dir, "daily-report")
        db.create_user("alice", str(cwd))

        mgr = CronSyncManager(db)
        mgr.sync_user("alice", str(cwd))

        # Sync again with same content
        result = mgr.sync_user("alice", str(cwd))
        assert result["unchanged"] == 1
        assert result["created"] == 0
        assert result["updated"] == 0
        assert result["deleted"] == 0


class TestSyncUpdated:
    def test_sync_updated_prompt(self, db: Database, tmp_path: Path):
        cwd = tmp_path / "cwd"
        cron_dir = cwd / ".agents" / "cron"
        _make_task(cron_dir, "daily-report", body="original prompt")
        db.create_user("alice", str(cwd))

        mgr = CronSyncManager(db)
        mgr.sync_user("alice", str(cwd))

        # Change prompt content (different hash)
        _make_task(cron_dir, "daily-report", body="updated prompt")
        result = mgr.sync_user("alice", str(cwd))

        assert result["updated"] == 1
        assert result["created"] == 0
        assert result["unchanged"] == 0


class TestSyncDeleted:
    def test_sync_deleted(self, db: Database, tmp_path: Path):
        cwd = tmp_path / "cwd"
        cron_dir = cwd / ".agents" / "cron"
        _make_task(cron_dir, "daily-report")
        db.create_user("alice", str(cwd))

        mgr = CronSyncManager(db)
        mgr.sync_user("alice", str(cwd))

        # Remove task from disk
        import shutil
        shutil.rmtree(cron_dir / "daily-report")

        result = mgr.sync_user("alice", str(cwd))
        assert result["deleted"] == 1
        assert result["created"] == 0

        # Verify soft-deleted in DB
        task = db.get_cron_task("alice:daily-report")
        assert task["deleted"] == 1

class TestSyncRestore:
    def test_sync_restore(self, db: Database, tmp_path: Path):
        cwd = tmp_path / "cwd"
        cron_dir = cwd / ".agents" / "cron"
        _make_task(cron_dir, "daily-report")
        db.create_user("alice", str(cwd))

        mgr = CronSyncManager(db)
        mgr.sync_user("alice", str(cwd))

        # Remove from disk → soft delete
        import shutil
        shutil.rmtree(cron_dir / "daily-report")
        mgr.sync_user("alice", str(cwd))
        assert db.get_cron_task("alice:daily-report")["deleted"] == 1

        # Re-add to disk → restore
        _make_task(cron_dir, "daily-report")
        result = mgr.sync_user("alice", str(cwd))
        assert result["created"] == 1  # restored counts as created

        task = db.get_cron_task("alice:daily-report")
        assert task["deleted"] == 0


class TestSyncMultipleTasks:
    def test_sync_multiple_tasks(self, db: Database, tmp_path: Path):
        cwd = tmp_path / "cwd"
        cron_dir = cwd / ".agents" / "cron"
        _make_task(cron_dir, "task-a")
        _make_task(cron_dir, "task-b")
        _make_task(cron_dir, "task-c")
        db.create_user("alice", str(cwd))

        mgr = CronSyncManager(db)
        result = mgr.sync_user("alice", str(cwd))

        assert result["created"] == 3
        assert result["updated"] == 0
        assert result["deleted"] == 0
        assert result["unchanged"] == 0

        tasks = db.list_cron_tasks("alice")
        assert len(tasks) == 3


class TestSyncScheduleChange:
    def test_sync_schedule_change(self, db: Database, tmp_path: Path):
        cwd = tmp_path / "cwd"
        cron_dir = cwd / ".agents" / "cron"
        _make_task(cron_dir, "daily-report", schedule="0 9 * * *")
        db.create_user("alice", str(cwd))

        mgr = CronSyncManager(db)
        mgr.sync_user("alice", str(cwd))

        # Change schedule expression
        _make_task(cron_dir, "daily-report", schedule="0 10 * * *")
        result = mgr.sync_user("alice", str(cwd))

        assert result["updated"] == 1
        task = db.get_cron_task("alice:daily-report")
        assert task["schedule"] == "0 10 * * *"


class TestSyncAllUsers:
    def test_sync_all_users(self, db: Database, tmp_path: Path):
        # Set up two users with CWDs
        cwd_a = tmp_path / "cwd_alice"
        cwd_b = tmp_path / "cwd_bob"
        _make_task(cwd_a / ".agents" / "cron", "task-a")
        _make_task(cwd_b / ".agents" / "cron", "task-b")
        _make_task(cwd_b / ".agents" / "cron", "task-c")

        db.create_user("alice", str(cwd_a))
        db.create_user("bob", str(cwd_b))

        mgr = CronSyncManager(db)
        results = mgr.sync_all_users()

        assert "alice" in results
        assert "bob" in results
        assert results["alice"]["created"] == 1
        assert results["bob"]["created"] == 2

        assert len(db.list_cron_tasks("alice")) == 1
        assert len(db.list_cron_tasks("bob")) == 2
