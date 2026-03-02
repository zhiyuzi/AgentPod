"""Tests for agentpod.db.Database."""

from __future__ import annotations

import os
from datetime import date

import pytest

from agentpod.db import Database


@pytest.fixture()
def db(tmp_path):
    """Create a Database backed by a temp SQLite file."""
    db_path = str(tmp_path / "test_registry.db")
    database = Database(db_path)
    database.init_db()
    yield database
    database.close()


class TestCreateAndQueryUser:
    def test_create_and_query_user(self, db: Database):
        api_key = db.create_user("alice", "/tmp/users/alice")
        assert api_key.startswith("sk-")
        assert len(api_key) == 3 + 32  # "sk-" + 32 hex chars

        user = db.get_user_by_api_key(api_key)
        assert user is not None
        assert user["id"] == "alice"
        assert user["cwd_path"] == "/tmp/users/alice"
        assert user["config"] == "{}"
        assert user["is_active"] == 1

    def test_get_user_by_id(self, db: Database):
        api_key = db.create_user("bob", "/tmp/users/bob")
        user = db.get_user_by_id("bob")
        assert user is not None
        assert user["api_key"] == api_key

    def test_get_nonexistent_user(self, db: Database):
        assert db.get_user_by_api_key("sk-nonexistent") is None
        assert db.get_user_by_id("nobody") is None

    def test_list_users(self, db: Database):
        db.create_user("u1", "/tmp/u1")
        db.create_user("u2", "/tmp/u2")
        users = db.list_users()
        assert len(users) == 2
        ids = [u["id"] for u in users]
        assert "u1" in ids
        assert "u2" in ids


class TestUpdateConfig:
    def test_update_config(self, db: Database):
        db.create_user("alice", "/tmp/alice")
        db.update_config("alice", '{"max_turns": 100}')
        user = db.get_user_by_id("alice")
        assert user is not None
        assert '"max_turns": 100' in user["config"]

    def test_update_config_preserves_other_fields(self, db: Database):
        api_key = db.create_user("bob", "/tmp/bob")
        db.update_config("bob", '{"key": "value"}')
        user = db.get_user_by_api_key(api_key)
        assert user is not None
        assert user["id"] == "bob"
        assert user["is_active"] == 1


class TestDisableUser:
    def test_disable_user(self, db: Database):
        db.create_user("alice", "/tmp/alice")
        db.disable_user("alice")
        user = db.get_user_by_id("alice")
        assert user is not None
        assert user["is_active"] == 0

    def test_enable_user(self, db: Database):
        db.create_user("alice", "/tmp/alice")
        db.disable_user("alice")
        db.enable_user("alice")
        user = db.get_user_by_id("alice")
        assert user is not None
        assert user["is_active"] == 1


class TestLogUsageAndDailyCost:
    def test_log_usage_and_daily_cost(self, db: Database):
        db.create_user("alice", "/tmp/alice")
        db.log_usage(
            user_id="alice",
            session_id="sess1",
            model="test-model",
            turns=3,
            input_tokens=1000,
            output_tokens=500,
            cached_tokens=200,
            cost_amount=0.05,
            duration_ms=3000,
        )
        db.log_usage(
            user_id="alice",
            session_id="sess2",
            model="test-model",
            turns=2,
            input_tokens=800,
            output_tokens=400,
            cached_tokens=100,
            cost_amount=0.03,
            duration_ms=2000,
        )
        daily = db.get_daily_cost("alice", target_date=date.today())
        assert abs(daily - 0.08) < 1e-6

    def test_get_usage_records(self, db: Database):
        db.create_user("alice", "/tmp/alice")
        db.log_usage("alice", "s1", "m1", 1, 100, 50, 0, 0.01, 500)
        rows = db.get_usage("alice")
        assert len(rows) == 1
        assert rows[0]["session_id"] == "s1"
        assert rows[0]["model"] == "m1"


class TestResetApiKey:
    def test_reset_api_key(self, db: Database):
        old_key = db.create_user("alice", "/tmp/alice")
        new_key = db.reset_api_key("alice")

        assert new_key.startswith("sk-")
        assert new_key != old_key

        # Old key should no longer work
        assert db.get_user_by_api_key(old_key) is None
        # New key should work
        user = db.get_user_by_api_key(new_key)
        assert user is not None
        assert user["id"] == "alice"


class TestCountUsers:
    def test_count_users_empty(self, db: Database):
        assert db.count_users() == 0

    def test_count_users(self, db: Database):
        db.create_user("u1", "/tmp/u1")
        db.create_user("u2", "/tmp/u2")
        assert db.count_users() == 2


class TestDailyStats:
    def test_daily_stats_empty(self, db: Database):
        stats = db.get_daily_stats()
        assert stats["total_queries"] == 0
        assert stats["total_input_tokens"] == 0
        assert stats["total_output_tokens"] == 0
        assert stats["total_cost"] == 0.0
        assert stats["active_users"] == 0

    def test_daily_stats_with_data(self, db: Database):
        db.create_user("alice", "/tmp/alice")
        db.create_user("bob", "/tmp/bob")
        db.log_usage("alice", "s1", "m1", 2, 1000, 200, 0, 0.05, 500)
        db.log_usage("alice", "s2", "m1", 1, 500, 100, 0, 0.02, 300)
        db.log_usage("bob", "s3", "m1", 3, 2000, 400, 0, 0.10, 800)

        stats = db.get_daily_stats()
        assert stats["total_queries"] == 3
        assert stats["total_input_tokens"] == 3500
        assert stats["total_output_tokens"] == 700
        assert abs(stats["total_cost"] - 0.17) < 1e-6
        assert stats["active_users"] == 2


class TestCronTasks:
    def _make_task(self, db, task_name="daily-report", user_id="alice", **overrides):
        defaults = dict(
            task_id=f"{user_id}:{task_name}",
            user_id=user_id,
            task_name=task_name,
            description="A test task",
            schedule="0 9 * * *",
            timezone="Asia/Shanghai",
            enabled=True,
            timeout=1200,
            max_turns=100,
            model="test-model",
            content_hash="abc123",
            next_run_at="2026-02-28T09:00:00+00:00",
        )
        defaults.update(overrides)
        db.upsert_cron_task(**defaults)
        return defaults["task_id"]

    def test_upsert_insert(self, db: Database):
        db.create_user("alice", "/tmp/alice")
        task_id = self._make_task(db)
        task = db.get_cron_task(task_id)
        assert task is not None
        assert task["user_id"] == "alice"
        assert task["task_name"] == "daily-report"
        assert task["schedule"] == "0 9 * * *"
        assert task["enabled"] == 1
        assert task["deleted"] == 0

    def test_upsert_update(self, db: Database):
        db.create_user("alice", "/tmp/alice")
        task_id = self._make_task(db)
        # Update with new description and schedule
        self._make_task(db, description="Updated", schedule="0 10 * * *")
        task = db.get_cron_task(task_id)
        assert task["description"] == "Updated"
        assert task["schedule"] == "0 10 * * *"

    def test_upsert_restores_deleted(self, db: Database):
        db.create_user("alice", "/tmp/alice")
        task_id = self._make_task(db)
        db.soft_delete_cron_task(task_id)
        assert db.get_cron_task(task_id)["deleted"] == 1
        # Re-upsert should restore
        self._make_task(db)
        assert db.get_cron_task(task_id)["deleted"] == 0

    def test_soft_delete(self, db: Database):
        db.create_user("alice", "/tmp/alice")
        task_id = self._make_task(db)
        db.soft_delete_cron_task(task_id)
        task = db.get_cron_task(task_id)
        assert task["deleted"] == 1

    def test_get_nonexistent(self, db: Database):
        assert db.get_cron_task("no:such") is None

    def test_list_excludes_deleted(self, db: Database):
        db.create_user("alice", "/tmp/alice")
        self._make_task(db, task_name="t1")
        self._make_task(db, task_name="t2")
        db.soft_delete_cron_task("alice:t2")
        tasks = db.list_cron_tasks("alice")
        assert len(tasks) == 1
        assert tasks[0]["task_name"] == "t1"

    def test_list_includes_deleted(self, db: Database):
        db.create_user("alice", "/tmp/alice")
        self._make_task(db, task_name="t1")
        self._make_task(db, task_name="t2")
        db.soft_delete_cron_task("alice:t2")
        tasks = db.list_cron_tasks("alice", include_deleted=True)
        assert len(tasks) == 2

    def test_list_all_cron_tasks(self, db: Database):
        db.create_user("alice", "/tmp/alice")
        db.create_user("bob", "/tmp/bob")
        self._make_task(db, task_name="t1", user_id="alice")
        self._make_task(db, task_name="t2", user_id="bob")
        tasks = db.list_all_cron_tasks()
        assert len(tasks) == 2

    def test_list_all_cron_tasks_include_deleted(self, db: Database):
        db.create_user("alice", "/tmp/alice")
        self._make_task(db, task_name="t1")
        self._make_task(db, task_name="t2")
        db.soft_delete_cron_task("alice:t2")
        assert len(db.list_all_cron_tasks()) == 1
        assert len(db.list_all_cron_tasks(include_deleted=True)) == 2

    def test_enable_disable(self, db: Database):
        db.create_user("alice", "/tmp/alice")
        task_id = self._make_task(db)
        db.disable_cron_task(task_id)
        assert db.get_cron_task(task_id)["enabled"] == 0
        db.enable_cron_task(task_id)
        assert db.get_cron_task(task_id)["enabled"] == 1

    def test_get_due_cron_tasks(self, db: Database):
        db.create_user("alice", "/tmp/alice")
        self._make_task(db, task_name="past", next_run_at="2020-01-01T00:00:00+00:00")
        self._make_task(db, task_name="future", next_run_at="2099-01-01T00:00:00+00:00")
        self._make_task(db, task_name="disabled", next_run_at="2020-01-01T00:00:00+00:00",
                        enabled=False)
        due = db.get_due_cron_tasks("2026-02-28T12:00:00+00:00")
        assert len(due) == 1
        assert due[0]["task_name"] == "past"

    def test_update_next_run(self, db: Database):
        db.create_user("alice", "/tmp/alice")
        task_id = self._make_task(db)
        db.update_cron_next_run(task_id, "2026-03-01T09:00:00+00:00")
        task = db.get_cron_task(task_id)
        assert task["next_run_at"] == "2026-03-01T09:00:00+00:00"


class TestCronRuns:
    def _setup_task(self, db):
        db.create_user("alice", "/tmp/alice")
        task_id = "alice:daily-report"
        db.upsert_cron_task(
            task_id=task_id, user_id="alice", task_name="daily-report",
            description="test", schedule="0 9 * * *", timezone="Asia/Shanghai",
            enabled=True, timeout=1200, max_turns=100, model="m1",
            content_hash="h1", next_run_at="2026-02-28T09:00:00+00:00",
        )
        return task_id

    def test_create_and_get(self, db: Database):
        task_id = self._setup_task(db)
        run_id = db.create_cron_run(task_id, "alice", "daily-report", "sess-1")
        assert isinstance(run_id, int)
        run = db.get_cron_run(run_id)
        assert run is not None
        assert run["task_id"] == task_id
        assert run["status"] == "running"
        assert run["session_id"] == "sess-1"

    def test_finish_cron_run(self, db: Database):
        task_id = self._setup_task(db)
        run_id = db.create_cron_run(task_id, "alice", "daily-report", "sess-1")
        db.finish_cron_run(
            run_id, status="completed", cost_amount=0.05,
            input_tokens=1000, output_tokens=500, turns=5, duration_ms=3000,
        )
        run = db.get_cron_run(run_id)
        assert run["status"] == "completed"
        assert run["finished_at"] is not None
        assert run["cost_amount"] == 0.05
        assert run["input_tokens"] == 1000
        assert run["turns"] == 5

    def test_finish_cron_run_with_error(self, db: Database):
        task_id = self._setup_task(db)
        run_id = db.create_cron_run(task_id, "alice", "daily-report", "sess-1")
        db.finish_cron_run(run_id, status="error", error_message="timeout")
        run = db.get_cron_run(run_id)
        assert run["status"] == "error"
        assert run["error_message"] == "timeout"

    def test_get_nonexistent_run(self, db: Database):
        assert db.get_cron_run(9999) is None

    def test_list_cron_runs_all(self, db: Database):
        task_id = self._setup_task(db)
        db.create_cron_run(task_id, "alice", "daily-report", "s1")
        db.create_cron_run(task_id, "alice", "daily-report", "s2")
        runs = db.list_cron_runs("alice")
        assert len(runs) == 2

    def test_list_cron_runs_by_task_name(self, db: Database):
        task_id = self._setup_task(db)
        # Add a second task
        db.upsert_cron_task(
            task_id="alice:cleanup", user_id="alice", task_name="cleanup",
            description="", schedule="0 0 * * *", timezone="UTC",
            enabled=True, timeout=600, max_turns=50, model="m1",
            content_hash="h2", next_run_at="2026-02-28T00:00:00+00:00",
        )
        db.create_cron_run(task_id, "alice", "daily-report", "s1")
        db.create_cron_run("alice:cleanup", "alice", "cleanup", "s2")
        runs = db.list_cron_runs("alice", task_name="daily-report")
        assert len(runs) == 1
        assert runs[0]["task_name"] == "daily-report"

    def test_list_cron_runs_limit(self, db: Database):
        task_id = self._setup_task(db)
        for i in range(5):
            db.create_cron_run(task_id, "alice", "daily-report", f"s{i}")
        runs = db.list_cron_runs("alice", limit=3)
        assert len(runs) == 3

    def test_list_all_cron_runs_no_filter(self, db: Database):
        task_id = self._setup_task(db)
        db.create_cron_run(task_id, "alice", "daily-report", "s1")
        runs = db.list_all_cron_runs()
        assert len(runs) == 1

    def test_list_all_cron_runs_filter_user(self, db: Database):
        task_id = self._setup_task(db)
        db.create_user("bob", "/tmp/bob")
        db.upsert_cron_task(
            task_id="bob:task", user_id="bob", task_name="task",
            description="", schedule="0 0 * * *", timezone="UTC",
            enabled=True, timeout=600, max_turns=50, model="m1",
            content_hash="h2", next_run_at="2026-02-28T00:00:00+00:00",
        )
        db.create_cron_run(task_id, "alice", "daily-report", "s1")
        db.create_cron_run("bob:task", "bob", "task", "s2")
        runs = db.list_all_cron_runs(user_id="alice")
        assert len(runs) == 1
        assert runs[0]["user_id"] == "alice"

    def test_list_all_cron_runs_filter_status(self, db: Database):
        task_id = self._setup_task(db)
        run1 = db.create_cron_run(task_id, "alice", "daily-report", "s1")
        db.create_cron_run(task_id, "alice", "daily-report", "s2")
        db.finish_cron_run(run1, status="completed")
        runs = db.list_all_cron_runs(status="running")
        assert len(runs) == 1
        assert runs[0]["session_id"] == "s2"

    def test_has_running_cron_run(self, db: Database):
        task_id = self._setup_task(db)
        assert db.has_running_cron_run(task_id) is False
        run_id = db.create_cron_run(task_id, "alice", "daily-report", "s1")
        assert db.has_running_cron_run(task_id) is True
        db.finish_cron_run(run_id, status="completed")
        assert db.has_running_cron_run(task_id) is False


class TestCronStats:
    def test_empty_stats(self, db: Database):
        stats = db.get_cron_stats()
        assert stats["total_tasks"] == 0
        assert stats["enabled_tasks"] == 0
        assert stats["active_runs"] == 0
        assert stats["runs_today"] == 0
        assert stats["cron_cost_today"] == 0.0

    def test_stats_with_data(self, db: Database):
        db.create_user("alice", "/tmp/alice")
        db.upsert_cron_task(
            task_id="alice:t1", user_id="alice", task_name="t1",
            description="", schedule="0 9 * * *", timezone="UTC",
            enabled=True, timeout=600, max_turns=50, model="m1",
            content_hash="h1", next_run_at="2026-02-28T09:00:00+00:00",
        )
        db.upsert_cron_task(
            task_id="alice:t2", user_id="alice", task_name="t2",
            description="", schedule="0 10 * * *", timezone="UTC",
            enabled=False, timeout=600, max_turns=50, model="m1",
            content_hash="h2", next_run_at="2026-02-28T10:00:00+00:00",
        )
        # Create a running run (today)
        db.create_cron_run("alice:t1", "alice", "t1", "s1")
        # Create a finished run (today)
        run2 = db.create_cron_run("alice:t1", "alice", "t1", "s2")
        db.finish_cron_run(run2, status="completed", cost_amount=0.10)

        stats = db.get_cron_stats()
        assert stats["total_tasks"] == 2
        assert stats["enabled_tasks"] == 1
        assert stats["active_runs"] == 1
        assert stats["runs_today"] == 2
        assert abs(stats["cron_cost_today"] - 0.10) < 1e-6
