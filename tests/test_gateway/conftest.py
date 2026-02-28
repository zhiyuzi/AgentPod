"""Shared fixtures for gateway tests."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import httpx

from agentpod.db import Database
from agentpod.gateway.admission import AdmissionController
from agentpod.gateway.app import app


@pytest.fixture()
def tmp_data_dir(tmp_path):
    """Temporary data directory with registry.db initialized."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    return data_dir


@pytest.fixture()
def tmp_cwd(tmp_path):
    """Temporary CWD directory with an AGENTS.md file."""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "AGENTS.md").write_text("# Test Agent\n", encoding="utf-8")
    (cwd / "hello.txt").write_text("hello world", encoding="utf-8")
    sub = cwd / "subdir"
    sub.mkdir()
    (sub / "nested.txt").write_text("nested content", encoding="utf-8")
    return cwd


@pytest.fixture()
def db(tmp_data_dir):
    """Database instance with schema initialized."""
    db = Database(str(tmp_data_dir / "registry.db"))
    db.init_db()
    yield db
    db.close()


@pytest.fixture()
def test_user(db, tmp_cwd):
    """Create a test user and return (user_dict, api_key)."""
    config = json.dumps({"writable_paths": ["docs/", "src/"], "max_concurrent": 2})
    api_key = db.create_user("test-user-1", str(tmp_cwd), config=config)
    user = db.get_user_by_api_key(api_key)
    return user, api_key


@pytest.fixture()
def disabled_user(db, tmp_cwd):
    """Create a disabled test user and return (user_dict, api_key)."""
    api_key = db.create_user("disabled-user", str(tmp_cwd))
    db.disable_user("disabled-user")
    user = db.get_user_by_api_key(api_key)
    return user, api_key


@pytest.fixture()
async def client(db, tmp_data_dir):
    """httpx AsyncClient bound to the FastAPI app with test state."""
    app.state.db = db
    app.state.config = type("C", (), {
        "max_concurrent": 5,
        "admin_key": "test-admin-key",
        "data_dir": str(tmp_data_dir),
    })()
    app.state.admission = AdmissionController(5)
    app.state.started_at = time.time()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac
