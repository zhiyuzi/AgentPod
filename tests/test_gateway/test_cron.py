"""Tests for gateway/cron.py – User Cron API."""

from __future__ import annotations

import pytest

ADMIN_HEADERS = {"Authorization": "Bearer test-admin-key"}


def _auth(api_key: str) -> dict:
    return {"Authorization": f"Bearer {api_key}"}


def _seed_task(db, user_id: str, task_name: str = "daily-report"):
    """Insert a cron task directly into DB for testing."""
    from datetime import datetime, timezone
    task_id = f"{user_id}:{task_name}"
    db.upsert_cron_task(
        task_id=task_id, user_id=user_id, task_name=task_name,
        description="test task", schedule="0 9 * * *", timezone="Asia/Shanghai",
        enabled=True, timeout=1200, max_turns=100, model="",
        content_hash="abc123", next_run_at=datetime.now(timezone.utc).isoformat(),
    )
    return task_id


# --- List tasks ---

@pytest.mark.asyncio
async def test_list_tasks_empty(client, test_user):
    user, api_key = test_user
    resp = await client.get("/v1/cron/tasks", headers=_auth(api_key))
    assert resp.status_code == 200
    assert resp.json()["tasks"] == []


@pytest.mark.asyncio
async def test_list_tasks(client, test_user, db):
    user, api_key = test_user
    _seed_task(db, user["id"])
    resp = await client.get("/v1/cron/tasks", headers=_auth(api_key))
    assert resp.status_code == 200
    tasks = resp.json()["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["task_name"] == "daily-report"


# --- Get task ---

@pytest.mark.asyncio
async def test_get_task(client, test_user, db):
    user, api_key = test_user
    _seed_task(db, user["id"])
    resp = await client.get("/v1/cron/tasks/daily-report", headers=_auth(api_key))
    assert resp.status_code == 200
    assert resp.json()["task"]["task_name"] == "daily-report"
    assert "recent_runs" in resp.json()


@pytest.mark.asyncio
async def test_get_task_not_found(client, test_user):
    user, api_key = test_user
    resp = await client.get("/v1/cron/tasks/nonexistent", headers=_auth(api_key))
    assert resp.status_code == 404


# --- Enable/Disable ---

@pytest.mark.asyncio
async def test_enable_disable(client, test_user, db):
    user, api_key = test_user
    _seed_task(db, user["id"])

    resp = await client.post("/v1/cron/tasks/daily-report/disable", headers=_auth(api_key))
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    resp = await client.post("/v1/cron/tasks/daily-report/enable", headers=_auth(api_key))
    assert resp.status_code == 200
    assert resp.json()["enabled"] is True


# --- Delete ---

@pytest.mark.asyncio
async def test_delete_task(client, test_user, db):
    user, api_key = test_user
    _seed_task(db, user["id"])

    resp = await client.delete("/v1/cron/tasks/daily-report", headers=_auth(api_key))
    assert resp.status_code == 200

    # Should be gone from list
    resp = await client.get("/v1/cron/tasks", headers=_auth(api_key))
    assert resp.json()["tasks"] == []


# --- Runs ---

@pytest.mark.asyncio
async def test_list_runs_empty(client, test_user):
    user, api_key = test_user
    resp = await client.get("/v1/cron/runs", headers=_auth(api_key))
    assert resp.status_code == 200
    assert resp.json()["runs"] == []


@pytest.mark.asyncio
async def test_get_run_not_found(client, test_user):
    user, api_key = test_user
    resp = await client.get("/v1/cron/runs/99999", headers=_auth(api_key))
    assert resp.status_code == 404


# --- Auth ---

@pytest.mark.asyncio
async def test_no_auth(client):
    resp = await client.get("/v1/cron/tasks")
    assert resp.status_code == 401


# --- Admin cron endpoints ---

@pytest.mark.asyncio
async def test_admin_list_cron_tasks(client, test_user, db):
    user, _ = test_user
    _seed_task(db, user["id"])
    resp = await client.get("/v1/admin/cron/tasks", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert len(resp.json()["tasks"]) >= 1


@pytest.mark.asyncio
async def test_admin_cron_stats(client, db):
    resp = await client.get("/v1/admin/stats", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert "cron" in data
    assert "total_tasks" in data["cron"]
