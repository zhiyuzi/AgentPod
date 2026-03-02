"""Tests for agentpod.cron.scheduler."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from agentpod.config import ServerConfig
from agentpod.cron.scheduler import CronScheduler
from agentpod.db import Database
from agentpod.types import Done, Error, UserInputRequired


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def db(tmp_path):
    db_path = str(tmp_path / "test.db")
    database = Database(db_path)
    database.init_db()
    yield database
    database.close()


@pytest.fixture()
def config():
    return ServerConfig(
        cron_enabled=True,
        cron_max_concurrent=2,
        cron_tick_interval=1,
        cron_sync_interval=300,
    )


# ---------------------------------------------------------------------------
# Mock runtime
# ---------------------------------------------------------------------------

class MockSessionMgr:
    def create_with_id(self, session_id, source="interactive"):
        return session_id


class MockRuntime:
    def __init__(self, cwd):
        self.session_mgr = MockSessionMgr()

    async def query(self, prompt, session_id, options):
        yield Done(
            usage={"input_tokens": 100, "output_tokens": 50, "turns": 1, "cached_tokens": 0},
            cost=0.01,
        )

    async def answer(self, session_id, tool_use_id, response):
        pass



def _make_task_file(cwd_path, task_name, prompt="do something"):
    """Create a TASK.md file on disk for a cron task."""
    task_dir = Path(cwd_path) / ".agents" / "cron" / task_name
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "TASK.md").write_text(
        f'---\nname: {task_name}\ndescription: test\nschedule: "0 9 * * *"\n---\n\n{prompt}\n',
        encoding="utf-8",
    )


def _insert_due_task(db: Database, user_id: str, task_name: str,
                     schedule: str = "0 9 * * *") -> str:
    """Insert a cron task into DB with next_run_at in the past (i.e. due now)."""
    task_id = f"{user_id}:{task_name}"
    past = "2020-01-01T00:00:00+00:00"
    db.upsert_cron_task(
        task_id=task_id, user_id=user_id, task_name=task_name,
        description="test", schedule=schedule, timezone="UTC",
        enabled=True, timeout=1200, max_turns=100, model="",
        content_hash="abc123", next_run_at=past,
    )
    return task_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSchedulerStartStop:
    async def test_start_stop_lifecycle(self, config, db):
        scheduler = CronScheduler(config, db, lambda u: MockRuntime(u["cwd_path"]))
        await scheduler.start()
        assert scheduler._tick_task is not None
        assert scheduler._sync_task is not None
        await scheduler.stop()
        assert scheduler._stopped is True

    async def test_start_disabled(self, db):
        cfg = ServerConfig(cron_enabled=False)
        scheduler = CronScheduler(cfg, db, lambda u: MockRuntime(u["cwd_path"]))
        await scheduler.start()
        # Background tasks should not be created when disabled
        assert scheduler._tick_task is None
        assert scheduler._sync_task is None



class TestTickDispatchesDueTask:
    async def test_tick_dispatches_due_task(self, config, db, tmp_path):
        cwd = str(tmp_path / "cwd")
        db.create_user("alice", cwd)
        _make_task_file(cwd, "daily-report")
        task_id = _insert_due_task(db, "alice", "daily-report")

        runtime = MockRuntime(cwd)
        scheduler = CronScheduler(config, db, lambda u: runtime)

        # Call _tick directly (not start())
        await scheduler._tick()

        # Give the dispatched task a moment to complete
        await asyncio.sleep(0.1)

        # Verify a cron_run was created and finished
        runs = db.list_cron_runs("alice")
        assert len(runs) == 1
        assert runs[0]["task_name"] == "daily-report"
        assert runs[0]["status"] == "completed"

        # Verify next_run_at was advanced
        task = db.get_cron_task(task_id)
        assert task["next_run_at"] > "2020-01-01"


class TestTickSkipsRunningTask:
    async def test_tick_skips_running_task(self, config, db, tmp_path):
        cwd = str(tmp_path / "cwd")
        db.create_user("alice", cwd)
        _make_task_file(cwd, "daily-report")
        task_id = _insert_due_task(db, "alice", "daily-report")

        scheduler = CronScheduler(config, db, lambda u: MockRuntime(cwd))
        # Simulate task already running in-process
        scheduler._running_tasks.add(task_id)

        await scheduler._tick()
        await asyncio.sleep(0.1)

        # No cron_run should be created
        runs = db.list_cron_runs("alice")
        assert len(runs) == 0


class TestTickSkipsBudgetExceeded:
    async def test_tick_skips_budget_exceeded(self, config, db, tmp_path):
        cwd = str(tmp_path / "cwd")
        user_config = json.dumps({"max_budget_daily": 1.0})
        db.create_user("alice", cwd, config=user_config)
        _make_task_file(cwd, "daily-report")
        task_id = _insert_due_task(db, "alice", "daily-report")

        # Log usage that exceeds the budget
        db.log_usage("alice", "sess1", "model", 1, 100, 50, 0, 1.5, 1000)

        scheduler = CronScheduler(config, db, lambda u: MockRuntime(cwd))
        await scheduler._tick()
        await asyncio.sleep(0.1)

        # No cron_run should be created
        runs = db.list_cron_runs("alice")
        assert len(runs) == 0

        # But next_run_at should be advanced
        task = db.get_cron_task(task_id)
        assert task["next_run_at"] > "2020-01-01"



class TestTickSkipsInactiveUser:
    async def test_tick_skips_inactive_user(self, config, db, tmp_path):
        cwd = str(tmp_path / "cwd")
        db.create_user("alice", cwd)
        db.disable_user("alice")
        _make_task_file(cwd, "daily-report")
        _insert_due_task(db, "alice", "daily-report")

        scheduler = CronScheduler(config, db, lambda u: MockRuntime(cwd))
        await scheduler._tick()
        await asyncio.sleep(0.1)

        runs = db.list_cron_runs("alice")
        assert len(runs) == 0


class TestTickSkipsHighMemory:
    async def test_tick_skips_high_memory(self, config, db, tmp_path):
        cwd = str(tmp_path / "cwd")
        db.create_user("alice", cwd)
        _make_task_file(cwd, "daily-report")
        _insert_due_task(db, "alice", "daily-report")

        scheduler = CronScheduler(config, db, lambda u: MockRuntime(cwd))

        # Mock psutil to report >90% memory
        mock_mem = MagicMock()
        mock_mem.percent = 95.0
        with patch("agentpod.cron.scheduler.psutil") as mock_psutil:
            mock_psutil.virtual_memory.return_value = mock_mem
            await scheduler._tick()

        await asyncio.sleep(0.1)

        # Entire tick should be skipped
        runs = db.list_cron_runs("alice")
        assert len(runs) == 0


class TestRunTaskRecordsUsage:
    async def test_run_task_records_usage(self, config, db, tmp_path):
        cwd = str(tmp_path / "cwd")
        db.create_user("alice", cwd)
        _make_task_file(cwd, "daily-report", prompt="generate report")
        task_id = _insert_due_task(db, "alice", "daily-report")

        runtime = MockRuntime(cwd)
        scheduler = CronScheduler(config, db, lambda u: runtime)

        task = db.get_cron_task(task_id)
        user = db.get_user_by_id("alice")
        await scheduler._run_task(task, user)

        # Verify cron_run was recorded
        runs = db.list_cron_runs("alice")
        assert len(runs) == 1
        run = runs[0]
        assert run["status"] == "completed"
        assert run["input_tokens"] == 100
        assert run["output_tokens"] == 50
        assert run["turns"] == 1

        # Verify usage_logs was recorded
        usage = db.get_usage("alice")
        assert len(usage) == 1
        assert usage[0]["session_id"].startswith("cron_daily-report_")

