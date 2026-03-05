"""Edge Agent — connects to AgentPod Gateway via WebSocket."""

from __future__ import annotations

import asyncio
import json
import sys

import websockets

from .tools import TOOLS


async def run(server_url: str, api_key: str):
    tools_info = [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in TOOLS
    ]
    tools_map = {t.name: t for t in TOOLS}

    while True:
        try:
            async with websockets.connect(server_url) as ws:
                # Auth
                await ws.send(json.dumps({"type": "auth", "api_key": api_key}, ensure_ascii=False))
                resp = json.loads(await ws.recv())

                if resp.get("type") == "auth_error":
                    print(f"Auth failed: {resp.get('message')}")
                    return

                print(f"Connected as {resp.get('user_id')}")

                # Message loop
                async for raw in ws:
                    msg = json.loads(raw)
                    msg_type = msg.get("type")
                    request_id = msg.get("request_id", "")

                    if msg_type == "tools_list":
                        await ws.send(json.dumps({
                            "type": "tools_list_result",
                            "request_id": request_id,
                            "tools": tools_info,
                        }, ensure_ascii=False))

                    elif msg_type == "tools_call":
                        tool_name = msg.get("tool_name", "")
                        tool_input = msg.get("input", {})
                        print(f"  -> {tool_name}({json.dumps(tool_input, ensure_ascii=False)})")

                        tool = tools_map.get(tool_name)
                        if tool is None:
                            await ws.send(json.dumps({
                                "type": "tools_call_result",
                                "request_id": request_id,
                                "content": f"Unknown tool: {tool_name}",
                                "is_error": True,
                            }, ensure_ascii=False))
                        else:
                            try:
                                result = tool.execute(tool_input)
                                await ws.send(json.dumps({
                                    "type": "tools_call_result",
                                    "request_id": request_id,
                                    "content": result,
                                    "is_error": False,
                                }, ensure_ascii=False))
                            except Exception as exc:
                                await ws.send(json.dumps({
                                    "type": "tools_call_result",
                                    "request_id": request_id,
                                    "content": str(exc),
                                    "is_error": True,
                                }, ensure_ascii=False))

        except (websockets.ConnectionClosed, ConnectionRefusedError, OSError) as e:
            print(f"Disconnected ({e}), reconnecting in 3s...")
            await asyncio.sleep(3)
