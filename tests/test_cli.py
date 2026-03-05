"""Tests for agentpod.cli commands."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run_cli(*args: str, env_override: dict | None = None) -> subprocess.CompletedProcess:
    """Run the agentpod CLI as a subprocess, returning the result."""
    env = os.environ.copy()
    # Ensure no provider keys leak into tests
    for key in ("VOLCENGINE_API_KEY", "ANTHROPIC_API_KEY", "ZHIPU_API_KEY", "MINIMAX_API_KEY"):
        env.pop(key, None)
    if env_override:
        env.update(env_override)
    return subprocess.run(
        [sys.executable, "-m", "agentpod", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


@pytest.fixture()
def data_dir(tmp_path):
    """Set up a temporary data directory with a valid template."""
    d = tmp_path / "data"
    d.mkdir()
    # Create template/
    tpl = d / "template"
    tpl.mkdir()
    (tpl / "AGENTS.md").write_text("# Test Agent\n", encoding="utf-8")
    (tpl / ".agents" / "skills").mkdir(parents=True)
    (tpl / "version").write_text("1.0.0\n", encoding="utf-8")
    return d


@pytest.fixture()
def cli_env(data_dir):
    """Return env dict pointing AGENTPOD_DATA_DIR at the temp data dir."""
    return {"AGENTPOD_DATA_DIR": str(data_dir)}


class TestCheck:
    def test_check_creates_data_dir_and_registry(self, tmp_path):
        data_dir = tmp_path / "fresh_data"
        # Create template so check doesn't warn about it
        tpl = data_dir / "template"
        tpl.mkdir(parents=True)
        (tpl / "AGENTS.md").write_text("# Agent\n", encoding="utf-8")

        result = _run_cli("check", env_override={"AGENTPOD_DATA_DIR": str(data_dir)})
        assert result.returncode == 0
        assert "registry.db initialized" in result.stdout
        assert (data_dir / "registry.db").exists()
        assert (data_dir / "users").is_dir()


class TestUserCreate:
    def test_user_create(self, cli_env, data_dir):
        result = _run_cli("user", "create", "testuser", env_override=cli_env)
        assert result.returncode == 0
        assert "API Key: sk-" in result.stdout
        assert "testuser" in result.stdout
        # Verify CWD was created
        user_dir = data_dir / "users" / "testuser"
        assert user_dir.is_dir()
        assert (user_dir / "AGENTS.md").is_file()
        assert (user_dir / "sessions").is_dir()

    def test_user_create_duplicate_fails(self, cli_env, data_dir):
        _run_cli("user", "create", "dup", env_override=cli_env)
        result = _run_cli("user", "create", "dup", env_override=cli_env)
        assert result.returncode != 0


class TestUserList:
    def test_user_list(self, cli_env):
        _run_cli("user", "create", "alice", env_override=cli_env)
        _run_cli("user", "create", "bob", env_override=cli_env)
        result = _run_cli("user", "list", env_override=cli_env)
        assert result.returncode == 0
        assert "alice" in result.stdout
        assert "bob" in result.stdout

    def test_user_list_empty(self, cli_env):
        # Run check first to init db
        _run_cli("check", env_override=cli_env)
        result = _run_cli("user", "list", env_override=cli_env)
        assert result.returncode == 0
        assert "No users found" in result.stdout


class TestUserConfig:
    def test_user_config_merge(self, cli_env):
        _run_cli("user", "create", "alice", env_override=cli_env)
        result = _run_cli(
            "user", "config", "alice", '{"max_turns": 100}',
            env_override=cli_env,
        )
        assert result.returncode == 0
        assert "max_turns" in result.stdout

        # Merge a second key
        result2 = _run_cli(
            "user", "config", "alice", '{"max_budget_daily": 10.0}',
            env_override=cli_env,
        )
        assert result2.returncode == 0
        # Both keys should be present
        assert "max_turns" in result2.stdout
        assert "max_budget_daily" in result2.stdout


class TestUserInfo:
    def test_user_info(self, cli_env):
        _run_cli("user", "create", "alice", env_override=cli_env)
        result = _run_cli("user", "info", "alice", env_override=cli_env)
        assert result.returncode == 0
        assert "alice" in result.stdout
        assert "API Key:" in result.stdout
        assert "active" in result.stdout


class TestStats:
    def test_stats_no_admin_key(self, cli_env):
        """stats without AGENTPOD_ADMIN_KEY should fail."""
        env = {**cli_env, "AGENTPOD_ADMIN_KEY": ""}
        result = _run_cli("stats", env_override=env)
        assert result.returncode != 0
        assert "AGENTPOD_ADMIN_KEY" in result.stderr

    def test_stats_server_not_running(self, cli_env):
        """stats with admin key but no server should fail gracefully."""
        env = {**cli_env, "AGENTPOD_ADMIN_KEY": "test-key", "AGENTPOD_PORT": "19999"}
        result = _run_cli("stats", env_override=env)
        assert result.returncode != 0
        assert "cannot connect" in result.stderr
