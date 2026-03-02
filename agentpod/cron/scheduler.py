"""Asyncio background scheduler for cron tasks."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil

from agentpod.config import ServerConfig
from agentpod.cron.sync import CronSyncManager, compute_next_run
from agentpod.db import Database
from agentpod.types import Done, Error, RuntimeOptions, UserInputRequired

_log = logging.getLogger("agentpod.cron")


class CronScheduler:
    """Background scheduler that ticks every N seconds and runs due cron tasks."""

    def __init__(self, config: ServerConfig, db: Database, get_runtime):
        """
        Args:
            config: Server configuration
            db: Database instance
            get_runtime: Callable(user_dict) -> AgentRuntime (reuses _runtimes cache)
        """
        self.config = config
        self.db = db
        self.get_runtime = get_runtime
        self._semaphore = asyncio.Semaphore(config.cron_max_concurrent)
        self._user_locks: dict[str, asyncio.Lock] = {}
        self._running_tasks: set[str] = set()  # task_ids currently executing
        self._tick_task: asyncio.Task | None = None
        self._sync_task: asyncio.Task | None = None
        self._stopped = False

    def _get_user_lock(self, user_id: str) -> asyncio.Lock:
        if user_id not in self._user_locks:
            self._user_locks[user_id] = asyncio.Lock()
        return self._user_locks[user_id]

    async def start(self):
        """Start the scheduler background loops."""
        if not self.config.cron_enabled:
            _log.info("Cron scheduler disabled by config")
            return
        _log.info(
            "Cron scheduler starting (tick=%ds, sync=%ds, max_concurrent=%d)",
            self.config.cron_tick_interval,
            self.config.cron_sync_interval,
            self.config.cron_max_concurrent,
        )
        self._stopped = False
        self._tick_task = asyncio.create_task(self._tick_loop())
        self._sync_task = asyncio.create_task(self._sync_loop())

    async def stop(self):
        """Stop the scheduler gracefully."""
        self._stopped = True
        for task in [self._tick_task, self._sync_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        _log.info("Cron scheduler stopped")

    async def _tick_loop(self):
        """Main tick loop: check for due tasks every tick_interval seconds."""
        while not self._stopped:
            try:
                await self._tick()
            except Exception:
                _log.exception("Cron tick error")
            await asyncio.sleep(self.config.cron_tick_interval)

    async def _sync_loop(self):
        """Periodic sync loop: sync all users' CWD -> DB."""
        while not self._stopped:
            await asyncio.sleep(self.config.cron_sync_interval)
            try:
                sync_mgr = CronSyncManager(self.db)
                results = sync_mgr.sync_all_users()
                total = sum(
                    s["created"] + s["updated"] + s["deleted"]
                    for s in results.values()
                )
                if total > 0:
                    _log.info("Cron sync: %d changes across %d users", total, len(results))
            except Exception:
                _log.exception("Cron sync error")

    async def _tick(self):
        """Single tick: find due tasks and dispatch them."""
        if psutil.virtual_memory().percent > 90:
            _log.warning("Cron tick skipped: memory > 90%%")
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        due_tasks = self.db.get_due_cron_tasks(now_iso)

        for task in due_tasks:
            task_id = task["id"]

            # Dedup: skip if already running in this process
            if task_id in self._running_tasks:
                continue
            # Dedup: skip if DB shows a running cron_run
            if self.db.has_running_cron_run(task_id):
                continue

            # Check user is valid and active
            user = self.db.get_user_by_id(task["user_id"])
            if not user or not user["is_active"]:
                continue

            # Budget check
            user_config = json.loads(user.get("config", "{}"))
            max_daily = user_config.get("max_budget_daily")
            if max_daily:
                daily_cost = self.db.get_daily_cost(task["user_id"])
                if daily_cost >= max_daily:
                    _log.info(
                        "Cron task '%s' skipped: user '%s' daily budget exceeded",
                        task["task_name"], task["user_id"],
                    )
                    # Advance next_run_at so we don't retry every tick
                    next_run = compute_next_run(task["schedule"], task["timezone"])
                    self.db.update_cron_next_run(task_id, next_run)
                    continue

            # Dispatch
            asyncio.create_task(self._execute_task(task, user))

    async def _execute_task(self, task: dict, user: dict):
        """Execute a single cron task with concurrency controls."""
        task_id = task["id"]
        user_id = task["user_id"]
        task_name = task["task_name"]

        self._running_tasks.add(task_id)
        user_lock = self._get_user_lock(user_id)

        try:
            async with self._semaphore:
                async with user_lock:
                    await self._run_task(task, user)
        except Exception:
            _log.exception("Cron task '%s' execution error", task_name)
        finally:
            self._running_tasks.discard(task_id)

    async def _run_task(self, task: dict, user: dict):
        """Core task execution logic."""
        task_id = task["id"]
        user_id = task["user_id"]
        task_name = task["task_name"]

        # Read prompt from TASK.md on disk
        cwd_path = user["cwd_path"]
        task_md = Path(cwd_path) / ".agents" / "cron" / task_name / "TASK.md"
        if not task_md.is_file():
            _log.warning("Cron task '%s': TASK.md not found at %s, skipping", task_name, task_md)
            next_run = compute_next_run(task["schedule"], task["timezone"])
            self.db.update_cron_next_run(task_id, next_run)
            return

        from agentpod.skills import load_frontmatter_and_body

        _, body = load_frontmatter_and_body(task_md)
        prompt = body.strip()
        if not prompt:
            _log.warning("Cron task '%s': empty prompt, skipping", task_name)
            next_run = compute_next_run(task["schedule"], task["timezone"])
            self.db.update_cron_next_run(task_id, next_run)
            return

        # Create session
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        session_id = f"cron_{task_name}_{ts}"

        runtime = self.get_runtime(user)
        runtime.session_mgr.create_with_id(session_id, source="cron")

        # Create run record
        run_id = self.db.create_cron_run(task_id, user_id, task_name, session_id)
        start_time = time.time()

        _log.info("Cron task '%s' started (run_id=%d, session=%s)", task_name, run_id, session_id)

        # Build options
        user_config = json.loads(user.get("config", "{}"))
        model = task["model"] or user_config.get("default_model", "doubao-seed-1-8-251228")
        options = RuntimeOptions(
            model=model,
            max_turns=task["max_turns"],
            context_window=user_config.get("context_window", 200000),
        )

        # Execute
        status = "completed"
        error_message = None
        usage_data: dict = {}
        cost = 0.0

        try:
            async for event in runtime.query(prompt, session_id, options):
                if isinstance(event, UserInputRequired):
                    await runtime.answer(session_id, event.tool_use_id, "[cron 自动跳过]")
                elif isinstance(event, Done):
                    usage_data = event.usage
                    cost = event.cost
                elif isinstance(event, Error):
                    if not error_message:
                        error_message = event.message
        except asyncio.TimeoutError:
            status = "timeout"
            error_message = f"Timeout after {task['timeout']}s"
        except Exception as e:
            status = "failed"
            error_message = str(e)
            _log.exception("Cron task '%s' failed", task_name)

        if error_message and status == "completed":
            status = "failed"

        duration_ms = int((time.time() - start_time) * 1000)

        # Finish run record
        self.db.finish_cron_run(
            run_id=run_id,
            status=status,
            error_message=error_message,
            cost_amount=cost,
            input_tokens=usage_data.get("input_tokens", 0),
            output_tokens=usage_data.get("output_tokens", 0),
            turns=usage_data.get("turns", 0),
            duration_ms=duration_ms,
        )

        # Log usage
        try:
            self.db.log_usage(
                user_id=user_id,
                session_id=session_id,
                model=model,
                turns=usage_data.get("turns", 0),
                input_tokens=usage_data.get("input_tokens", 0),
                output_tokens=usage_data.get("output_tokens", 0),
                cached_tokens=usage_data.get("cached_tokens", 0),
                cost_amount=cost,
                duration_ms=duration_ms,
            )
        except Exception:
            _log.exception("Failed to log cron usage")

        # Update next_run_at
        next_run = compute_next_run(task["schedule"], task["timezone"])
        self.db.update_cron_next_run(task_id, next_run)

        _log.info(
            "Cron task '%s' %s (run_id=%d, duration=%dms, cost=%.4f)",
            task_name, status, run_id, duration_ms, cost,
        )
