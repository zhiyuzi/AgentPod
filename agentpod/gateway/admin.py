"""Admin API router for user management and system stats."""

from __future__ import annotations

import json
import shutil
import time
from datetime import date
from pathlib import Path

import psutil
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
        "budget": u.get("budget", 0.0),
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


@router.post("/users/{user_id}/budget")
async def add_budget(user_id: str, request: Request):
    db: Database = request.app.state.db
    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, f"User not found: {user_id}")

    body = await request.json()
    amount = body.get("amount")
    if not isinstance(amount, (int, float)) or amount <= 0:
        raise HTTPException(400, "amount must be a positive number")

    new_budget = db.add_budget(user_id, float(amount))
    return {"user_id": user_id, "budget": new_budget}


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


@router.get("/stats")
async def stats(request: Request):
    db: Database = request.app.state.db
    config = request.app.state.config
    data_dir = Path(config.data_dir)

    # System resources
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage(str(data_dir))

    # Runtime state
    from agentpod.gateway.app import _runtimes

    admission = request.app.state.admission
    started_at = getattr(request.app.state, "started_at", time.time())
    active_connections = sum(admission._user_counts.values())

    # Today's usage
    daily = db.get_daily_stats()

    # Cron stats
    cron_stats = db.get_cron_stats()

    # Edge connections
    from agentpod.edge import edge_manager

    return {
        "system": {
            "cpu_percent": psutil.cpu_percent(interval=0),
            "memory_percent": mem.percent,
            "memory_total_mb": round(mem.total / (1024 * 1024)),
            "disk_percent": disk.percent,
            "disk_total_gb": round(disk.total / (1024 ** 3)),
        },
        "runtime": {
            "uptime_seconds": round(time.time() - started_at),
            "active_connections": active_connections,
            "semaphore_available": admission.semaphore._value,
            "loaded_runtimes": len(_runtimes),
        },
        "usage_today": {
            "total_queries": daily["total_queries"],
            "total_input_tokens": daily["total_input_tokens"],
            "total_output_tokens": daily["total_output_tokens"],
            "total_cost": round(daily["total_cost"], 6),
            "active_users": daily["active_users"],
            "total_users": db.count_users(),
        },
        "cron": cron_stats,
        "edge": edge_manager.snapshot(),
    }


# --- Cron Admin ---

@router.post("/cron/tasks", status_code=201)
async def create_cron_task_admin(request: Request):
    from agentpod.cron.sync import CronSyncManager
    from agentpod.cron.writer import create_cron_task

    db: Database = request.app.state.db
    config = request.app.state.config
    body = await request.json()

    user_id = str(body.get("user_id", "")).strip()
    if not user_id:
        raise HTTPException(400, "user_id is required")
    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, f"User not found: {user_id}")

    name = str(body.get("name", "")).strip()
    description = str(body.get("description", "")).strip()
    schedule = str(body.get("schedule", "")).strip()
    prompt = str(body.get("prompt", "")).strip()
    if not all([name, description, schedule, prompt]):
        raise HTTPException(400, "Required fields: user_id, name, description, schedule, prompt")

    try:
        create_cron_task(
            user["cwd_path"],
            name=name, description=description, schedule=schedule, prompt=prompt,
            timezone=body.get("timezone", "Asia/Shanghai"),
            enabled=body.get("enabled", True),
            timeout=body.get("timeout", 1200),
            max_turns=body.get("max_turns", 0),
            model=body.get("model", ""),
            min_interval=config.cron_min_interval,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except FileExistsError:
        raise HTTPException(409, f"Task already exists: {name}")

    sync_mgr = CronSyncManager(db, min_interval=config.cron_min_interval)
    sync_mgr.sync_user(user_id, user["cwd_path"])

    task_id = f"{user_id}:{name}"
    db_task = db.get_cron_task(task_id)
    return {"task": db_task}


@router.put("/cron/tasks/{task_id}")
async def update_cron_task_admin(task_id: str, request: Request):
    from agentpod.cron.sync import CronSyncManager
    from agentpod.cron.writer import update_cron_task

    db: Database = request.app.state.db
    config = request.app.state.config

    if ":" not in task_id:
        raise HTTPException(400, "task_id must be in format 'user_id:task_name'")
    user_id, task_name = task_id.split(":", 1)

    user = db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, f"User not found: {user_id}")

    body = await request.json()
    try:
        update_cron_task(
            user["cwd_path"], task_name,
            description=body.get("description"),
            schedule=body.get("schedule"),
            prompt=body.get("prompt"),
            timezone=body.get("timezone"),
            enabled=body.get("enabled"),
            timeout=body.get("timeout"),
            max_turns=body.get("max_turns"),
            model=body.get("model"),
            min_interval=config.cron_min_interval,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except FileNotFoundError:
        raise HTTPException(404, f"Task not found: {task_name}")

    sync_mgr = CronSyncManager(db, min_interval=config.cron_min_interval)
    sync_mgr.sync_user(user_id, user["cwd_path"])

    db_task = db.get_cron_task(task_id)
    return {"task": db_task}


@router.get("/cron/tasks")
async def list_cron_tasks(request: Request):
    db: Database = request.app.state.db
    user_id = request.query_params.get("user_id")
    if user_id:
        tasks = db.list_cron_tasks(user_id)
    else:
        tasks = db.list_all_cron_tasks()
    return {"tasks": tasks}


@router.get("/cron/runs")
async def list_cron_runs(request: Request):
    db: Database = request.app.state.db
    user_id = request.query_params.get("user_id")
    status = request.query_params.get("status")
    runs = db.list_all_cron_runs(user_id=user_id, status=status)
    return {"runs": runs}


@router.post("/cron/tasks/{task_id}/disable")
async def disable_cron_task(task_id: str, request: Request):
    db: Database = request.app.state.db
    task = db.get_cron_task(task_id)
    if not task:
        raise HTTPException(404, f"Task not found: {task_id}")
    db.disable_cron_task(task_id)
    return {"status": "ok", "task_id": task_id, "enabled": False}


@router.post("/cron/tasks/{task_id}/enable")
async def enable_cron_task(task_id: str, request: Request):
    db: Database = request.app.state.db
    task = db.get_cron_task(task_id)
    if not task:
        raise HTTPException(404, f"Task not found: {task_id}")
    db.enable_cron_task(task_id)
    return {"status": "ok", "task_id": task_id, "enabled": True}


@router.delete("/cron/tasks/{task_id}")
async def delete_cron_task(task_id: str, request: Request):
    from agentpod.cron.writer import delete_cron_task_files

    db: Database = request.app.state.db
    task = db.get_cron_task(task_id)
    if not task:
        raise HTTPException(404, f"Task not found: {task_id}")
    db.soft_delete_cron_task(task_id)
    user = db.get_user_by_id(task["user_id"])
    if user:
        try:
            delete_cron_task_files(user["cwd_path"], task["task_name"])
        except FileNotFoundError:
            pass
    return {"status": "ok", "task_id": task_id}


@router.post("/cron/sync")
async def sync_all_cron(request: Request):
    from agentpod.cron.sync import CronSyncManager
    db: Database = request.app.state.db
    sync_mgr = CronSyncManager(db, min_interval=request.app.state.config.cron_min_interval)
    results = sync_mgr.sync_all_users()
    return {"status": "ok", "results": results}
# --- Webhook Dead Letters ---

@router.get("/webhooks/dead-letters")
async def list_dead_letters(request: Request):
    db: Database = request.app.state.db
    limit = int(request.query_params.get("limit", "50"))
    letters = db.list_dead_letters(limit=limit)
    return {"dead_letters": letters}


@router.post("/webhooks/dead-letters/{dl_id}/retry")
async def retry_dead_letter(dl_id: int, request: Request):
    from agentpod.gateway.webhook import emit_event

    db: Database = request.app.state.db
    config = request.app.state.config
    dl = db.get_dead_letter(dl_id)
    if not dl:
        raise HTTPException(404, f"Dead letter not found: {dl_id}")

    import json
    payload = json.loads(dl["payload"])
    event_type = dl["event_type"]

    # Re-emit (runs in background with retries)
    import asyncio
    asyncio.create_task(emit_event(
        event_type, payload, db,
        webhook_url=config.webhook_url,
        webhook_secret=config.webhook_secret,
    ))

    # Remove from dead letters since it's being retried
    db.delete_dead_letter(dl_id)
    return {"status": "ok", "event_id": dl["event_id"]}
