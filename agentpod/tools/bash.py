"""BashTool - execute shell commands."""

from __future__ import annotations

import asyncio
from pathlib import Path

from agentpod.tools.base import Tool, ToolResult


class BashTool(Tool):
    name = "bash"
    description = "Execute a shell command."
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute"},
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 120)",
                "default": 120,
            },
        },
        "required": ["command"],
    }

    async def execute(self, input: dict, cwd: Path) -> ToolResult:
        command = input["command"]
        timeout = input.get("timeout", 120)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except Exception as e:
            return ToolResult(content=f"Failed to start process: {e}", is_error=True)

        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            output = stdout.decode("utf-8", errors="replace") if stdout else ""
            if proc.returncode != 0:
                return ToolResult(
                    content=f"{output}\n[exit code: {proc.returncode}]",
                    is_error=True,
                )
            return ToolResult(content=output)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return ToolResult(
                content=f"Command timed out after {timeout} seconds",
                is_error=True,
            )
