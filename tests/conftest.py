"""Shared test fixtures."""

import pytest
import shutil
from pathlib import Path
from agentpod.db import Database


@pytest.fixture
def tmp_cwd(tmp_path):
    """Create a temporary CWD with full structure."""
    cwd = tmp_path / "test_cwd"
    # Copy example_cwd
    src = Path(__file__).parent.parent / "example_cwd"
    if src.exists():
        shutil.copytree(src, cwd)
    else:
        cwd.mkdir()
        (cwd / "AGENTS.md").write_text("# Test Agent\n\n你是一个测试助手。")
        (cwd / ".agents" / "skills" / "hello").mkdir(parents=True)
        (cwd / ".agents" / "skills" / "hello" / "SKILL.md").write_text("Hello skill")
        (cwd / "version").write_text("1.0.0\n")
    (cwd / "sessions").mkdir(exist_ok=True)
    return cwd


@pytest.fixture
def db(tmp_path):
    """Create a temporary database."""
    db_path = str(tmp_path / "test_registry.db")
    database = Database(db_path)
    database.init_db()
    yield database
    database.close()


@pytest.fixture
def test_user(db, tmp_cwd):
    """Create a test user with API key."""
    api_key = db.create_user("testuser", str(tmp_cwd))
    return {"id": "testuser", "api_key": api_key, "cwd_path": str(tmp_cwd)}
