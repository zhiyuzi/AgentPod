"""Edge Gateway WebSocket endpoint for Edge Agent connections."""

from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from agentpod.logging import get_logger
from agentpod.edge import edge_manager

router = APIRouter()
logger = get_logger("gateway.edge")


@router.websocket("/v1/edge/connect")
async def edge_connect(websocket: WebSocket):
    await websocket.accept()
    user_id: str | None = None

    try:
        # First message must be auth
        raw = await websocket.receive_text()
        msg = json.loads(raw)

        if msg.get("type") != "auth" or not msg.get("api_key"):
            await websocket.send_text(
                json.dumps({"type": "auth_error", "message": "First message must be auth with api_key"}, ensure_ascii=False)
            )
            await websocket.close()
            return

        # Validate API key using the same DB logic as HTTP auth
        db = websocket.app.state.db
        user = db.get_user_by_api_key(msg["api_key"])
        if user is None or not user["is_active"]:
            await websocket.send_text(
                json.dumps({"type": "auth_error", "message": "Invalid or inactive API key"}, ensure_ascii=False)
            )
            await websocket.close()
            return

        user_id = user["id"]
        conn = edge_manager.add(user_id, websocket)
        logger.info("edge_connected", extra={"user_id": user_id})

        await websocket.send_text(
            json.dumps({"type": "auth_ok", "user_id": user_id}, ensure_ascii=False)
        )

        # Receive loop: route responses back to pending futures
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            request_id = data.get("request_id")
            if request_id:
                conn.resolve(request_id, data)

    except WebSocketDisconnect:
        logger.info("edge_disconnected", extra={"user_id": user_id or "unknown"})
    except Exception as e:
        logger.warning("edge_error", extra={"user_id": user_id or "unknown", "error": str(e)})
    finally:
        if user_id:
            edge_manager.remove(user_id)
