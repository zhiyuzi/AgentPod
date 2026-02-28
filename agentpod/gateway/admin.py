"""Admin API router for user management."""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from agentpod.db import Database
from agentpod.gateway.auth import get_admin

router = APIRouter(prefix="/v1/admin", dependencies=[Depends(get_admin)])


def _key_prefix(api_key: str) -> str:
    return api_key[:7] + "..." if api_key else "n/a"


def _user_summary(u: dict) -> dict:
    return {
        "id": u["id"],
        "api_key_prefix": _key_prefix(u["api_key"]),
        "cwd_path": u["cwd_path"],
        "config": json.loads(u["config"]) if isinstance(u["config"], str) else u["config"],
        "is_active": bool(u["is_active"]),
        "created_at": u["created_at"],
        "updated_at": u["updated_at"],
    }


@router.post("/users", status_code=201)
async def create_user(request: Request):
    db: Database = request.app.state.db
    config = request.app.state.config
    data_dir = Path(config.data_dir)
    template_dir = data_dir / "template"

    body = await request.json()
    user_id = body.get("user_id", "").strip()
    if not user_id:
        raise HTTPException(400, "user_id is required")

    # Check template
    if not template_dir.is_dir() or not (template_dir / "AGENTS.md").is_file():
        raise HTTPException(500, "Server template directory is not configured")

    user_dir = data_dir / "users" / user_id
    if user_dir.exists():
        raise HTTPException(409, f"User already exists: {user_id}")

    # Copy template -> users/{id}/
    shutil.copytree(str(template_dir), str(user_dir))
    (user_dir / "sessions").mkdir(exist_ok=True)

    cwd_path = str(user_dir.resolve())
    api_key = db.create_user(user_id, cwd_path)

    return {"user_id": user_id, "api_key": api_key, "cwd_path": cwd_path}


@router.get("/users")
async def list_users(request: Request):
    db: Database = request.app.state.db
    users = db.list_users()
    return {"users": [_user_summary(u) for u in users]}


@router.get("/users/{user_id}")
async def get_user(user_id: str, request: Request):
    db: Database = request.app.state.db
    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, f"User not found: {user_id}")
    return _user_summary(user)


@router.patch("/users/{user_id}")
async def update_user(user_id: str, request: Request):
    db: Database = request.app.state.db
    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, f"User not found: {user_id}")

    body = await request.json()
    incoming_config = body.get("config", {})
    if not isinstance(incoming_config, dict):
        raise HTTPException(400, "config must be a JSON object")

    existing = json.loads(user["config"]) if isinstance(user["config"], str) else user["config"]
    existing.update(incoming_config)
    merged = json.dumps(existing, ensure_ascii=False)
    db.update_config(user_id, merged)

    return {"status": "ok", "config": existing}


@router.post("/users/{user_id}/disable")
async def disable_user(user_id: str, request: Request):
    db: Database = request.app.state.db
    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, f"User not found: {user_id}")
    db.disable_user(user_id)
    return {"status": "ok", "user_id": user_id, "is_active": False}


@router.post("/users/{user_id}/enable")
async def enable_user(user_id: str, request: Request):
    db: Database = request.app.state.db
    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, f"User not found: {user_id}")
    db.enable_user(user_id)
    return {"status": "ok", "user_id": user_id, "is_active": True}


@router.post("/users/{user_id}/reset-key")
async def reset_key(user_id: str, request: Request):
    db: Database = request.app.state.db
    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, f"User not found: {user_id}")
    new_key = db.reset_api_key(user_id)
    return {"user_id": user_id, "api_key": new_key}


@router.get("/users/{user_id}/usage")
async def get_usage(user_id: str, request: Request):
    db: Database = request.app.state.db
    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, f"User not found: {user_id}")

    params = request.query_params
    from_date = params.get("from")
    to_date = params.get("to")
    month = params.get("month")
    all_records = params.get("all")

    if month:
        from_date = month + "-01"
        year, mon = map(int, month.split("-"))
        if mon == 12:
            to_date = f"{year + 1}-01-01"
        else:
            to_date = f"{year}-{mon + 1:02d}-01"
    elif not from_date and not to_date and not all_records:
        from_date = date.today().isoformat()

    if all_records:
        from_date = None
        to_date = None

    rows = db.get_usage(user_id, from_date=from_date, to_date=to_date)

    total_input = sum(r["input_tokens"] for r in rows)
    total_output = sum(r["output_tokens"] for r in rows)
    total_cost = sum(r["cost_amount"] for r in rows)

    return {
        "user_id": user_id,
        "records": rows,
        "summary": {
            "count": len(rows),
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cost": round(total_cost, 6),
        },
    }
