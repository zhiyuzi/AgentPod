"""Admission controller – concurrency, budget, and resource checks."""

from __future__ import annotations

import asyncio
import json

import psutil
from fastapi import HTTPException


class AdmissionController:
    def __init__(self, max_concurrent: int):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._user_counts: dict[str, int] = {}

    async def check_system_resources(self):
        if psutil.virtual_memory().percent > 90:
            raise HTTPException(503, "System resources exhausted")

    async def check_budget(self, user: dict, db):
        budget = user.get("budget", 0.0)
        if budget <= 0:
            raise HTTPException(403, "Budget exhausted")

    async def check_daily_budget(self, user: dict, db):
        config = json.loads(user.get("config", "{}"))
        max_daily = config.get("max_budget_daily")
        if max_daily:
            daily_cost = db.get_daily_cost(user["id"])
            if daily_cost >= max_daily:
                raise HTTPException(403, f"Daily budget exceeded: {daily_cost:.2f} >= {max_daily}")

    async def check_user_concurrent(self, user: dict):
        config = json.loads(user.get("config", "{}"))
        max_concurrent = config.get("max_concurrent", 2)
        current = self._user_counts.get(user["id"], 0)
        if current >= max_concurrent:
            raise HTTPException(429, "User concurrent limit reached")

    def increment_user(self, user_id: str):
        self._user_counts[user_id] = self._user_counts.get(user_id, 0) + 1

    def decrement_user(self, user_id: str):
        self._user_counts[user_id] = max(0, self._user_counts.get(user_id, 0) - 1)

    @property
    def semaphore(self):
        return self._semaphore
