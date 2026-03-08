"""Tests for gateway/admin.py – Admin API."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

ADMIN_HEADERS = {"Authorization": "Bearer test-admin-key"}


@pytest.fixture(autouse=True)
def _setup_template(tmp_data_dir):
    """Create template and users directories for admin create tests."""
    template = tmp_data_dir / "template"
    template.mkdir(exist_ok=True)
    (template / "AGENTS.md").write_text("# Test Agent\n", encoding="utf-8")
    (template / "version").write_text("1.0.0\n", encoding="utf-8")
    (tmp_data_dir / "users").mkdir(exist_ok=True)


# --- Auth ---

@pytest.mark.asyncio
async def test_admin_no_auth(client):
    resp = await client.get("/v1/admin/users")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_admin_wrong_key(client):
    resp = await client.get(
        "/v1/admin/users", headers={"Authorization": "Bearer wrong-key"}
    )
    assert resp.status_code == 401


# --- Create ---

@pytest.mark.asyncio
async def test_create_user(client):
    resp = await client.post(
        "/v1/admin/users", json={"user_id": "alice"}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["user_id"] == "alice"
    assert data["api_key"].startswith("sk-")
    assert "alice" in data["cwd_path"]


@pytest.mark.asyncio
async def test_create_user_duplicate(client):
    await client.post(
        "/v1/admin/users", json={"user_id": "bob"}, headers=ADMIN_HEADERS
    )
    resp = await client.post(
        "/v1/admin/users", json={"user_id": "bob"}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_create_user_empty_id(client):
    resp = await client.post(
        "/v1/admin/users", json={"user_id": ""}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 400


# --- List ---

@pytest.mark.asyncio
async def test_list_users_empty(client):
    resp = await client.get("/v1/admin/users", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["users"] == []


@pytest.mark.asyncio
async def test_list_users(client):
    await client.post(
        "/v1/admin/users", json={"user_id": "carol"}, headers=ADMIN_HEADERS
    )
    resp = await client.get("/v1/admin/users", headers=ADMIN_HEADERS)
    users = resp.json()["users"]
    assert len(users) >= 1
    u = [x for x in users if x["id"] == "carol"][0]
    assert u["api_key_prefix"].startswith("sk-")
    assert u["is_active"] is True


# --- Get ---

@pytest.mark.asyncio
async def test_get_user(client):
    await client.post(
        "/v1/admin/users", json={"user_id": "dave"}, headers=ADMIN_HEADERS
    )
    resp = await client.get("/v1/admin/users/dave", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["id"] == "dave"


@pytest.mark.asyncio
async def test_get_user_not_found(client):
    resp = await client.get("/v1/admin/users/nobody", headers=ADMIN_HEADERS)
    assert resp.status_code == 404


# --- Update config ---

@pytest.mark.asyncio
async def test_update_config(client):
    await client.post(
        "/v1/admin/users", json={"user_id": "eve"}, headers=ADMIN_HEADERS
    )
    resp = await client.patch(
        "/v1/admin/users/eve",
        json={"config": {"max_budget_daily": 5.0}},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["config"]["max_budget_daily"] == 5.0


# --- Disable / Enable ---

@pytest.mark.asyncio
async def test_disable_enable(client):
    await client.post(
        "/v1/admin/users", json={"user_id": "frank"}, headers=ADMIN_HEADERS
    )
    resp = await client.post("/v1/admin/users/frank/disable", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["is_active"] is False

    resp = await client.post("/v1/admin/users/frank/enable", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["is_active"] is True


# --- Reset key ---

@pytest.mark.asyncio
async def test_reset_key(client):
    create_resp = await client.post(
        "/v1/admin/users", json={"user_id": "grace"}, headers=ADMIN_HEADERS
    )
    old_key = create_resp.json()["api_key"]

    resp = await client.post("/v1/admin/users/grace/reset-key", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    new_key = resp.json()["api_key"]
    assert new_key.startswith("sk-")
    assert new_key != old_key


# --- Usage ---

@pytest.mark.asyncio
async def test_usage_empty(client, db):
    await client.post(
        "/v1/admin/users", json={"user_id": "hank"}, headers=ADMIN_HEADERS
    )
    resp = await client.get(
        "/v1/admin/users/hank/usage?all=true", headers=ADMIN_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["summary"]["count"] == 0


# --- Stats ---

@pytest.mark.asyncio
async def test_stats(client):
    resp = await client.get("/v1/admin/stats", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()

    # system
    sys = data["system"]
    assert 0 <= sys["cpu_percent"] <= 100
    assert 0 <= sys["memory_percent"] <= 100
    assert sys["memory_total_mb"] > 0
    assert sys["disk_total_gb"] > 0

    # runtime
    rt = data["runtime"]
    assert rt["uptime_seconds"] >= 0
    assert rt["active_connections"] == 0
    assert rt["semaphore_available"] > 0
    assert rt["loaded_runtimes"] >= 0

    # usage_today
    usage = data["usage_today"]
    assert usage["total_queries"] == 0
    assert usage["total_cost"] == 0
    assert usage["total_users"] == 0

    # edge
    edge = data["edge"]
    assert edge["count"] == 0
    assert edge["connected_users"] == []


@pytest.mark.asyncio
async def test_stats_with_usage(client, db):
    db.log_usage(
        user_id="stats-user",
        session_id="s1",
        model="test-model",
        turns=3,
        input_tokens=1000,
        output_tokens=200,
        cached_tokens=0,
        cost_amount=0.05,
        duration_ms=500,
    )
    resp = await client.get("/v1/admin/stats", headers=ADMIN_HEADERS)
    usage = resp.json()["usage_today"]
    assert usage["total_queries"] == 1
    assert usage["total_input_tokens"] == 1000
    assert usage["total_output_tokens"] == 200
    assert usage["total_cost"] == 0.05
    assert usage["active_users"] == 1
    assert usage["total_users"] >= 0


@pytest.mark.asyncio
async def test_stats_no_auth(client):
    resp = await client.get("/v1/admin/stats")
    assert resp.status_code == 401


# --- Budget ---

@pytest.mark.asyncio
async def test_add_budget(client, db):
    await client.post(
        "/v1/admin/users", json={"user_id": "budget-user"}, headers=ADMIN_HEADERS
    )
    resp = await client.post(
        "/v1/admin/users/budget-user/budget",
        json={"amount": 10.0},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["budget"] == 10.0

    # Accumulate
    resp = await client.post(
        "/v1/admin/users/budget-user/budget",
        json={"amount": 5.0},
        headers=ADMIN_HEADERS,
    )
    assert resp.json()["budget"] == 15.0


@pytest.mark.asyncio
async def test_add_budget_invalid_amount(client):
    await client.post(
        "/v1/admin/users", json={"user_id": "budget-inv"}, headers=ADMIN_HEADERS
    )
    resp = await client.post(
        "/v1/admin/users/budget-inv/budget",
        json={"amount": -5},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_add_budget_user_not_found(client):
    resp = await client.post(
        "/v1/admin/users/nobody/budget",
        json={"amount": 1.0},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_user_includes_budget(client, db):
    await client.post(
        "/v1/admin/users", json={"user_id": "bud-view"}, headers=ADMIN_HEADERS
    )
    db.add_budget("bud-view", 7.5)
    resp = await client.get("/v1/admin/users/bud-view", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["budget"] == 7.5


# --- Dead Letters ---

@pytest.mark.asyncio
async def test_list_dead_letters_empty(client):
    resp = await client.get(
        "/v1/admin/webhooks/dead-letters", headers=ADMIN_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["dead_letters"] == []


@pytest.mark.asyncio
async def test_list_dead_letters(client, db):
    db.insert_dead_letter("evt_1", "query_done", '{"test": 1}', 4, "timeout")
    resp = await client.get(
        "/v1/admin/webhooks/dead-letters", headers=ADMIN_HEADERS
    )
    assert resp.status_code == 200
    letters = resp.json()["dead_letters"]
    assert len(letters) == 1
    assert letters[0]["event_id"] == "evt_1"


@pytest.mark.asyncio
async def test_retry_dead_letter_not_found(client):
    resp = await client.post(
        "/v1/admin/webhooks/dead-letters/9999/retry", headers=ADMIN_HEADERS
    )
    assert resp.status_code == 404
