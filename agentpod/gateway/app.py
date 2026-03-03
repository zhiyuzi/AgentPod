"""Main FastAPI application for AgentPod gateway."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

from agentpod.config import load_server_config
from agentpod.db import Database
from agentpod.gateway.admission import AdmissionController
from agentpod.gateway.auth import get_current_user
from agentpod.gateway.admin import router as admin_router
from agentpod.gateway.cron import router as cron_router
from agentpod.gateway.cwd import router as cwd_router
from agentpod.gateway.preflight import run_preflight
from agentpod.gateway.sse import event_to_sse
from agentpod.logging import get_logger
from agentpod.types import Done, MessageStart, RuntimeOptions, TurnComplete

logger = get_logger("gateway")

# Runtime instance cache
_runtimes: dict[str, "AgentRuntime"] = {}  # noqa: F821


def _get_runtime(user: dict):
    from agentpod.runtime.runtime import AgentRuntime

    user_id = user["id"]
    if user_id not in _runtimes:
        _runtimes[user_id] = AgentRuntime(Path(user["cwd_path"]), config=app.state.config)
        # Sync cron tasks on first runtime load
        try:
            from agentpod.cron.sync import CronSyncManager
            if hasattr(app, 'state') and hasattr(app.state, 'db'):
                sync_mgr = CronSyncManager(app.state.db)
                sync_mgr.sync_user(user_id, user["cwd_path"])
        except Exception:
            pass
    return _runtimes[user_id]


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = load_server_config()

    # Preflight
    results = await run_preflight(config)
    for r in results:
        symbol = "+" if r.status == "pass" else "!" if r.status == "warn" else "x"
        logger.info(f"preflight: {symbol} {r.message}")

    # Init DB
    db = Database(str(Path(config.data_dir) / "registry.db"))
    db.init_db()
    app.state.db = db
    app.state.config = config
    app.state.admission = AdmissionController(config.max_concurrent)
    app.state.started_at = time.time()

    # Start cron scheduler
    from agentpod.cron.scheduler import CronScheduler
    from agentpod.cron.sync import CronSyncManager

    # Initial sync for all users
    if config.cron_enabled:
        sync_mgr = CronSyncManager(db)
        sync_mgr.sync_all_users()

    scheduler = CronScheduler(config, db, _get_runtime)
    app.state.cron_scheduler = scheduler
    await scheduler.start()

    yield

    # Stop cron scheduler
    await scheduler.stop()

    # Shutdown
    db.close()


app = FastAPI(title="AgentPod", version="0.1.0", lifespan=lifespan)

# Include routers
app.include_router(cwd_router)
app.include_router(admin_router)
app.include_router(cron_router)


@app.get("/v1/health")
async def health():
    return {"status": "ok"}


@app.post("/v1/query")
async def query(request: Request, user: dict = Depends(get_current_user)):
    admission: AdmissionController = request.app.state.admission
    db: Database = request.app.state.db

    # Admission checks
    await admission.check_system_resources()
    await admission.check_daily_budget(user, db)
    await admission.check_user_concurrent(user)

    body = await request.json()
    content = body.get("content", "")  # str or list[dict] for multimodal
    session_id = body.get("session_id")
    model = body.get("model")

    # Build RuntimeOptions from user config
    config = json.loads(user.get("config", "{}"))
    options = RuntimeOptions(
        model=model or config.get("default_model", "doubao-seed-1-8-251228"),
        max_turns=config.get("max_turns", 50),
        max_budget_usd=config.get("max_budget_per_session"),
        context_window=config.get("context_window", 200000),
    )

    runtime = _get_runtime(user)
    admission.increment_user(user["id"])

    start_time = time.time()
    logger.info(
        "query_start",
        extra={"user_id": user["id"], "session_id": session_id, "model": options.model},
    )

    async def event_gen():
        nonlocal session_id
        last_usage = {}
        last_cost = 0.0
        last_turn = 0
        try:
            async with admission.semaphore:
                async for event in runtime.query(content, session_id, options):
                    sse = event_to_sse(event)
                    if sse:
                        yield sse
                    # Capture actual session_id from MessageStart
                    if isinstance(event, MessageStart):
                        session_id = event.session_id
                    # Track latest usage from TurnComplete for fallback
                    if isinstance(event, TurnComplete):
                        last_usage = event.usage
                        last_cost = event.cost
                        last_turn = event.turn
                    # Log usage on Done
                    if isinstance(event, Done):
                        duration_ms = int((time.time() - start_time) * 1000)
                        logger.info(
                            "query_done",
                            extra={
                                "user_id": user["id"],
                                "session_id": session_id,
                                "model": options.model,
                                "input_tokens": event.usage.get("input_tokens", 0),
                                "output_tokens": event.usage.get("output_tokens", 0),
                                "cost": event.cost,
                                "stop_reason": event.stop_reason,
                                "duration_ms": duration_ms,
                            },
                        )
                        try:
                            db.log_usage(
                                user_id=user["id"],
                                session_id=session_id or "unknown",
                                model=options.model,
                                turns=event.usage.get("turns", 0),
                                input_tokens=event.usage.get("input_tokens", 0),
                                output_tokens=event.usage.get("output_tokens", 0),
                                cached_tokens=event.usage.get("cached_tokens", 0),
                                cost_amount=event.cost,
                                duration_ms=duration_ms,
                            )
                        except Exception:
                            pass
        except asyncio.CancelledError:
            # Client disconnected — fallback: log partial usage from last TurnComplete
            if last_usage:
                duration_ms = int((time.time() - start_time) * 1000)
                logger.info(
                    "query_cancelled",
                    extra={
                        "user_id": user["id"],
                        "session_id": session_id,
                        "model": options.model,
                        "input_tokens": last_usage.get("input_tokens", 0),
                        "output_tokens": last_usage.get("output_tokens", 0),
                        "cost": last_cost,
                        "duration_ms": duration_ms,
                    },
                )
                try:
                    db.log_usage(
                        user_id=user["id"],
                        session_id=session_id or "unknown",
                        model=options.model,
                        turns=last_turn,
                        input_tokens=last_usage.get("input_tokens", 0),
                        output_tokens=last_usage.get("output_tokens", 0),
                        cached_tokens=last_usage.get("cached_tokens", 0),
                        cost_amount=last_cost,
                        duration_ms=duration_ms,
                    )
                except Exception:
                    pass
        finally:
            admission.decrement_user(user["id"])

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/v1/answer")
async def answer(request: Request, user: dict = Depends(get_current_user)):
    body = await request.json()
    session_id = body.get("session_id")
    tool_use_id = body.get("tool_use_id")
    response = body.get("response", "")

    runtime = _get_runtime(user)
    await runtime.answer(session_id, tool_use_id, response)
    return {"status": "ok"}


@app.get("/v1/sessions")
async def list_sessions(request: Request, user: dict = Depends(get_current_user)):
    runtime = _get_runtime(user)
    sessions = await runtime.list_sessions()
    return {
        "sessions": [
            {
                "session_id": s.session_id,
                "created_at": s.created_at,
                "parent_session_id": s.parent_session_id,
            }
            for s in sessions
        ]
    }


@app.get("/v1/sessions/{session_id}")
async def get_session(
    session_id: str, request: Request, user: dict = Depends(get_current_user)
):
    runtime = _get_runtime(user)
    meta = await runtime.resume_session(session_id)
    messages = runtime.session_mgr.load(session_id)
    return {
        "meta": {
            "session_id": meta.session_id,
            "created_at": meta.created_at,
            "parent_session_id": meta.parent_session_id,
        },
        "messages": messages,
    }


@app.post("/v1/sessions/{session_id}/fork")
async def fork_session(
    session_id: str, request: Request, user: dict = Depends(get_current_user)
):
    runtime = _get_runtime(user)
    new_id = await runtime.fork_session(session_id)
    return {"session_id": new_id}


@app.get("/v1/context/{session_id}")
async def get_context(
    session_id: str, request: Request, user: dict = Depends(get_current_user)
):
    runtime = _get_runtime(user)
    config = json.loads(user.get("config", "{}"))
    context_window = config.get("context_window", 200000)
    snapshot = await runtime.get_context_info(session_id, context_window)
    return {
        "context_window": snapshot.context_window,
        "system_prompt_tokens": snapshot.system_prompt_tokens,
        "tools_tokens": snapshot.tools_tokens,
        "messages_tokens": snapshot.messages_tokens,
        "reserved_output_tokens": snapshot.reserved_output_tokens,
        "used_tokens": snapshot.used_tokens,
        "available_tokens": snapshot.available_tokens,
        "usage_ratio": snapshot.usage_ratio,
        "message_count": snapshot.message_count,
    }


@app.get("/v1/me")
async def me(request: Request, user: dict = Depends(get_current_user)):
    config = json.loads(user.get("config", "{}"))
    return {
        "id": user["id"],
        "is_active": bool(user["is_active"]),
        "config": config,
        "created_at": user["created_at"],
    }


@app.get("/v1/usage")
async def usage(request: Request, user: dict = Depends(get_current_user)):
    db: Database = request.app.state.db
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

    rows = db.get_usage(user["id"], from_date=from_date, to_date=to_date)

    total_input = sum(r["input_tokens"] for r in rows)
    total_output = sum(r["output_tokens"] for r in rows)
    total_cost = sum(r["cost_amount"] for r in rows)

    return {
        "records": rows,
        "summary": {
            "count": len(rows),
            "input_tokens": total_input,
            "output_tokens": total_output,
            "cost": round(total_cost, 6),
        },
    }
