"""CWD (current working directory) file management API router."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from agentpod.gateway.auth import get_current_user
from agentpod.tools.base import safe_resolve

router = APIRouter(prefix="/v1/cwd")

SYSTEM_PROTECTED = {".agents", "AGENTS.md", "version", "sessions"}


def _is_system_protected(path: str) -> bool:
    parts = Path(path).parts
    return bool(parts) and parts[0] in SYSTEM_PROTECTED


def _is_writable(path: str, writable_paths: list[str]) -> bool:
    if not writable_paths:
        return False
    return any(path.startswith(wp) for wp in writable_paths)


@router.get("/{path:path}")
async def read_cwd(path: str, request: Request, user: dict = Depends(get_current_user)):
    cwd = Path(user["cwd_path"])
    resolved = safe_resolve(path or ".", cwd)

    if resolved.is_dir():
        entries = []
        for item in sorted(resolved.iterdir()):
            entry: dict = {"name": item.name, "type": "directory" if item.is_dir() else "file"}
            if item.is_file():
                stat = item.stat()
                entry["size"] = stat.st_size
                entry["mtime"] = datetime.fromtimestamp(stat.st_mtime, UTC).isoformat()
            elif item.is_dir():
                entry["mtime"] = datetime.fromtimestamp(item.stat().st_mtime, UTC).isoformat()
            entries.append(entry)
        return {"path": path, "type": "directory", "entries": entries}
    elif resolved.is_file():
        stat = resolved.stat()
        content = resolved.read_text(encoding="utf-8")
        return {
            "path": path,
            "type": "file",
            "size": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
            "content": content,
        }
    else:
        raise HTTPException(404, f"Path not found: {path}")


@router.put("/{path:path}")
async def write_cwd(path: str, request: Request, user: dict = Depends(get_current_user)):
    cwd = Path(user["cwd_path"])
    config = json.loads(user.get("config", "{}"))
    writable_paths = config.get("writable_paths", [])

    if _is_system_protected(path):
        raise HTTPException(403, f"System protected path: {path}")
    if not _is_writable(path, writable_paths):
        raise HTTPException(403, f"Path not in writable paths: {path}")

    resolved = safe_resolve(path, cwd)
    body = await request.json()
    content = body.get("content", "")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    return {"status": "ok", "path": path}


@router.delete("/{path:path}")
async def delete_cwd(path: str, request: Request, user: dict = Depends(get_current_user)):
    cwd = Path(user["cwd_path"])
    config = json.loads(user.get("config", "{}"))
    writable_paths = config.get("writable_paths", [])

    if _is_system_protected(path):
        raise HTTPException(403, f"System protected path: {path}")
    if not _is_writable(path, writable_paths):
        raise HTTPException(403, f"Path not in writable paths: {path}")

    resolved = safe_resolve(path, cwd)
    if resolved.is_file():
        resolved.unlink()
    elif resolved.is_dir():
        resolved.rmdir()  # Only empty dirs
    else:
        raise HTTPException(404, f"Path not found: {path}")
    return {"status": "ok", "path": path}


@router.post("/")
async def create_cwd(request: Request, user: dict = Depends(get_current_user)):
    cwd = Path(user["cwd_path"])
    body = await request.json()
    path = body.get("path", "")
    item_type = body.get("type", "file")

    config = json.loads(user.get("config", "{}"))
    writable_paths = config.get("writable_paths", [])

    if _is_system_protected(path):
        raise HTTPException(403, f"System protected path: {path}")
    if not _is_writable(path, writable_paths):
        raise HTTPException(403, f"Path not in writable paths: {path}")

    resolved = safe_resolve(path, cwd)
    if item_type == "directory":
        resolved.mkdir(parents=True, exist_ok=True)
    else:
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.touch()
    return {"status": "ok", "path": path, "type": item_type}
