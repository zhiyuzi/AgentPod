"""Authentication dependencies for FastAPI routes."""

from __future__ import annotations

from fastapi import HTTPException, Request

from agentpod.db import Database


async def get_current_user(request: Request) -> dict:
    """Extract and validate the Bearer token from the Authorization header."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    api_key = auth[7:]

    db: Database = request.app.state.db
    user = db.get_user_by_api_key(api_key)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="User account is disabled")
    return user


async def get_admin(request: Request) -> None:
    """Validate the Admin Key from the Authorization header."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = auth[7:]

    admin_key = request.app.state.config.admin_key
    if not admin_key:
        raise HTTPException(status_code=501, detail="Admin API is not configured")
    if token != admin_key:
        raise HTTPException(status_code=401, detail="Invalid admin key")
