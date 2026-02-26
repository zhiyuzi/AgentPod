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
