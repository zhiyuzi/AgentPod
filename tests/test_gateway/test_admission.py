"""Tests for gateway/admission.py – budget and concurrency checks."""

from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from agentpod.gateway.admission import AdmissionController


@pytest.mark.asyncio
async def test_daily_budget_exceeded(db, tmp_cwd):
    """When daily cost >= max_budget_daily the admission controller should raise 403."""
    config = json.dumps({"max_budget_daily": 1.0})
    api_key = db.create_user("budget-user", str(tmp_cwd), config=config)
    user = db.get_user_by_api_key(api_key)

    # Log some usage that exceeds the budget
    db.log_usage(
        user_id="budget-user",
        session_id="s1",
        model="test-model",
        turns=1,
        input_tokens=100,
        output_tokens=50,
        cached_tokens=0,
        cost_amount=1.5,
        duration_ms=100,
    )

    admission = AdmissionController(max_concurrent=5)
    with pytest.raises(HTTPException) as exc_info:
        await admission.check_daily_budget(user, db)
    assert exc_info.value.status_code == 403
    assert "Daily budget exceeded" in exc_info.value.detail


@pytest.mark.asyncio
async def test_budget_exhausted(db):
    """When budget <= 0 the admission controller should raise 403."""
    api_key = db.create_user("broke-user", "/tmp/broke")
    user = db.get_user_by_api_key(api_key)

    admission = AdmissionController(max_concurrent=5)
    with pytest.raises(HTTPException) as exc_info:
        await admission.check_budget(user, db)
    assert exc_info.value.status_code == 403
    assert "Budget exhausted" in exc_info.value.detail


@pytest.mark.asyncio
async def test_budget_ok(db):
    """When budget > 0 the check should pass without raising."""
    api_key = db.create_user("rich-user", "/tmp/rich")
    db.add_budget("rich-user", 10.0)
    user = db.get_user_by_api_key(api_key)

    admission = AdmissionController(max_concurrent=5)
    # Should not raise
    await admission.check_budget(user, db)


@pytest.mark.asyncio
async def test_user_concurrent_limit():
    """When user hits their concurrent limit, should raise 429."""
    admission = AdmissionController(max_concurrent=10)
    user = {"id": "u1", "config": json.dumps({"max_concurrent": 1})}

    admission.increment_user("u1")
    with pytest.raises(HTTPException) as exc_info:
        await admission.check_user_concurrent(user)
    assert exc_info.value.status_code == 429

    admission.decrement_user("u1")
    # Should not raise now
    await admission.check_user_concurrent(user)
