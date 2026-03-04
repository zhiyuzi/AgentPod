"""Edge connection manager — module-level singleton.

Placed at the top level of agentpod/ to avoid circular imports between
gateway and runtime packages.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field

from fastapi import WebSocket


@dataclass
class EdgeConnection:
    """A single Edge Agent WebSocket connection."""

    user_id: str
    websocket: WebSocket
    _pending: dict[str, asyncio.Future] = field(default_factory=dict)

    async def request(self, msg: dict, timeout: float = 30) -> dict:
        """Send a request and wait for the matching response."""
        request_id = uuid.uuid4().hex[:12]
        msg["request_id"] = request_id
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future
        try:
            await self.websocket.send_text(json.dumps(msg, ensure_ascii=False))
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"Edge request {request_id} timed out after {timeout}s")
        finally:
            self._pending.pop(request_id, None)

    def resolve(self, request_id: str, response: dict):
        """Resolve a pending future with the Edge Agent's response."""
        future = self._pending.get(request_id)
        if future and not future.done():
            future.set_result(response)


class EdgeConnectionManager:
    """Manages user_id → EdgeConnection mappings."""

    def __init__(self):
        self._connections: dict[str, EdgeConnection] = {}

    def add(self, user_id: str, websocket: WebSocket) -> EdgeConnection:
        conn = EdgeConnection(user_id=user_id, websocket=websocket)
        self._connections[user_id] = conn
        return conn

    def remove(self, user_id: str):
        self._connections.pop(user_id, None)

    def get(self, user_id: str) -> EdgeConnection | None:
        return self._connections.get(user_id)

    def snapshot(self) -> dict:
        """Return current connection status for admin/stats."""
        return {
            "count": len(self._connections),
            "connected_users": sorted(self._connections.keys()),
        }


# Module-level singleton
edge_manager = EdgeConnectionManager()
