"""Tests for gateway/app.py – top-level app endpoints."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_me(client, test_user):
    _, api_key = test_user
    resp = await client.get("/v1/me", headers={"Authorization": f"Bearer {api_key}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "test-user-1"
    assert data["is_active"] is True
    assert "config" in data
    assert "created_at" in data
    # Should NOT expose sensitive fields
    assert "api_key" not in data
    assert "cwd_path" not in data


@pytest.mark.asyncio
async def test_me_no_auth(client):
    resp = await client.get("/v1/me")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_usage(client, test_user):
    _, api_key = test_user
    resp = await client.get(
        "/v1/usage?all=true", headers={"Authorization": f"Bearer {api_key}"}
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "records" in data
    assert "summary" in data
    assert data["summary"]["count"] == 0


@pytest.mark.asyncio
async def test_usage_no_auth(client):
    resp = await client.get("/v1/usage")
    assert resp.status_code == 401
