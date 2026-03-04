"""BashTool - execute shell commands in a sandboxed environment."""

from __future__ import annotations

import asyncio
from pathlib import Path

from agentpod.sandbox.isolate import run_sandboxed, sandbox_available
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

    def __init__(
        self,
        shared_dir: Path | None = None,
        sandbox_memory_max: str = "",
        sandbox_cpu_quota: str = "",
        sandbox_pids_max: str = "",
    ):
        self.shared_dir = shared_dir
        self.sandbox_memory_max = sandbox_memory_max
        self.sandbox_cpu_quota = sandbox_cpu_quota
        self.sandbox_pids_max = sandbox_pids_max

    async def execute(self, input: dict, cwd: Path) -> ToolResult:
        command = input["command"]
        timeout = input.get("timeout", 120)

        try:
            output, returncode = await run_sandboxed(
                command, cwd, timeout=timeout, shared_dir=self.shared_dir,
                memory_max=self.sandbox_memory_max,
                cpu_quota=self.sandbox_cpu_quota,
                pids_max=self.sandbox_pids_max,
            )
        except Exception as e:
            return ToolResult(content=f"Failed to start process: {e}", is_error=True)

        if returncode == -1:
            # Timeout
            return ToolResult(content=output, is_error=True)

        if returncode != 0:
            return ToolResult(
                content=f"{output}\n[exit code: {returncode}]",
                is_error=True,
            )

        return ToolResult(content=output)
