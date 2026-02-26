"""Tests for gateway/auth.py – Bearer token authentication."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_no_auth_header(client):
    resp = await client.get("/v1/health")
    assert resp.status_code == 200

    resp = await client.get("/v1/sessions")
    assert resp.status_code == 401
    assert "Missing or invalid" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_invalid_api_key(client):
    resp = await client.get(
        "/v1/sessions", headers={"Authorization": "Bearer sk-bogus"}
    )
    assert resp.status_code == 401
    assert "Invalid API key" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_disabled_user(client, disabled_user):
    _, api_key = disabled_user
    resp = await client.get(
        "/v1/sessions", headers={"Authorization": f"Bearer {api_key}"}
    )
    assert resp.status_code == 403
    assert "disabled" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_valid_api_key(client, test_user):
    """A valid key should not get a 401/403 on a protected endpoint.

    We hit /v1/sessions which requires auth. The runtime is not set up so
    we may get a 500, but the auth layer itself should pass (no 401/403).
    """
    _, api_key = test_user
    resp = await client.get(
        "/v1/sessions", headers={"Authorization": f"Bearer {api_key}"}
    )
    # Auth passed – we should NOT get 401 or 403
    assert resp.status_code not in (401, 403)
