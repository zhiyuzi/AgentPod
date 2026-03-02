"""User-facing cron API router."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from agentpod.db import Database
from agentpod.gateway.auth import get_current_user

router = APIRouter(prefix="/v1/cron", dependencies=[Depends(get_current_user)])


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
    db: Database = request.app.state.db
    task_id = f"{user['id']}:{name}"
    task = db.get_cron_task(task_id)
    if not task or task["deleted"]:
        raise HTTPException(404, f"Task not found: {name}")
    db.soft_delete_cron_task(task_id)
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
    sync_mgr = CronSyncManager(db)
    result = sync_mgr.sync_user(user["id"], user["cwd_path"])
    return {"status": "ok", **result}
