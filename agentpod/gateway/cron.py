"""User-facing cron API router."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from agentpod.db import Database
from agentpod.gateway.auth import get_current_user

router = APIRouter(prefix="/v1/cron", dependencies=[Depends(get_current_user)])


@router.post("/tasks", status_code=201)
async def create_task(request: Request, user: dict = Depends(get_current_user)):
    from agentpod.cron.sync import CronSyncManager
    from agentpod.cron.writer import create_cron_task

    db: Database = request.app.state.db
    config = request.app.state.config
    body = await request.json()

    name = str(body.get("name", "")).strip()
    description = str(body.get("description", "")).strip()
    schedule = str(body.get("schedule", "")).strip()
    prompt = str(body.get("prompt", "")).strip()
    if not all([name, description, schedule, prompt]):
        raise HTTPException(400, "Required fields: name, description, schedule, prompt")

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
    sync_mgr.sync_user(user["id"], user["cwd_path"])

    task_id = f"{user['id']}:{name}"
    db_task = db.get_cron_task(task_id)
    return {"task": db_task}


@router.put("/tasks/{name}")
async def update_task(name: str, request: Request, user: dict = Depends(get_current_user)):
    from agentpod.cron.sync import CronSyncManager
    from agentpod.cron.writer import update_cron_task

    db: Database = request.app.state.db
    config = request.app.state.config
    body = await request.json()

    try:
        update_cron_task(
            user["cwd_path"], name,
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
        raise HTTPException(404, f"Task not found: {name}")

    sync_mgr = CronSyncManager(db, min_interval=config.cron_min_interval)
    sync_mgr.sync_user(user["id"], user["cwd_path"])

    task_id = f"{user['id']}:{name}"
    db_task = db.get_cron_task(task_id)
    return {"task": db_task}


@router.get("/tasks")
async def list_tasks(request: Request, user: dict = Depends(get_current_user)):
    db: Database = request.app.state.db
    tasks = db.list_cron_tasks(user["id"])
    return {"tasks": tasks}


@router.get("/tasks/{name}")
async def get_task(name: str, request: Request, user: dict = Depends(get_current_user)):
    db: Database = request.app.state.db
    task_id = f"{user['id']}:{name}"
    task = db.get_cron_task(task_id)
    if not task or task["deleted"]:
        raise HTTPException(404, f"Task not found: {name}")
    # Include recent runs
    runs = db.list_cron_runs(user["id"], task_name=name, limit=10)
    return {"task": task, "recent_runs": runs}


@router.post("/tasks/{name}/enable")
async def enable_task(name: str, request: Request, user: dict = Depends(get_current_user)):
    db: Database = request.app.state.db
    task_id = f"{user['id']}:{name}"
    task = db.get_cron_task(task_id)
    if not task or task["deleted"]:
        raise HTTPException(404, f"Task not found: {name}")
    db.enable_cron_task(task_id)
    return {"status": "ok", "task_name": name, "enabled": True}


@router.post("/tasks/{name}/disable")
async def disable_task(name: str, request: Request, user: dict = Depends(get_current_user)):
    db: Database = request.app.state.db
    task_id = f"{user['id']}:{name}"
    task = db.get_cron_task(task_id)
    if not task or task["deleted"]:
        raise HTTPException(404, f"Task not found: {name}")
    db.disable_cron_task(task_id)
    return {"status": "ok", "task_name": name, "enabled": False}


@router.delete("/tasks/{name}")
async def delete_task(name: str, request: Request, user: dict = Depends(get_current_user)):
    from agentpod.cron.writer import delete_cron_task_files

    db: Database = request.app.state.db
    task_id = f"{user['id']}:{name}"
    task = db.get_cron_task(task_id)
    if not task or task["deleted"]:
        raise HTTPException(404, f"Task not found: {name}")
    db.soft_delete_cron_task(task_id)
    try:
        delete_cron_task_files(user["cwd_path"], name)
    except FileNotFoundError:
        pass  # disk files already gone — DB soft-delete still applies
    return {"status": "ok", "task_name": name}


@router.get("/runs")
async def list_runs(request: Request, user: dict = Depends(get_current_user)):
    db: Database = request.app.state.db
    task_name = request.query_params.get("task")
    runs = db.list_cron_runs(user["id"], task_name=task_name)
    return {"runs": runs}


@router.get("/runs/{run_id}")
async def get_run(run_id: int, request: Request, user: dict = Depends(get_current_user)):
    db: Database = request.app.state.db
    run = db.get_cron_run(run_id)
    if not run or run["user_id"] != user["id"]:
        raise HTTPException(404, f"Run not found: {run_id}")
    return run


@router.post("/sync")
async def sync_tasks(request: Request, user: dict = Depends(get_current_user)):
    from agentpod.cron.sync import CronSyncManager
    db: Database = request.app.state.db
    sync_mgr = CronSyncManager(db, min_interval=request.app.state.config.cron_min_interval)
    result = sync_mgr.sync_user(user["id"], user["cwd_path"])
    return {"status": "ok", **result}
