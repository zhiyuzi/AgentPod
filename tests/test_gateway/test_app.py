"""Tests for gateway/app.py – top-level app endpoints."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/v1/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
