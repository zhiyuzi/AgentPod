"""TodoWriteTool - manage a structured task list."""

from __future__ import annotations

import json
from pathlib import Path

from agentpod.tools.base import Tool, ToolResult


class TodoWriteTool(Tool):
    name = "todo_write"
    description = "Create or update a structured todo list."
    input_schema = {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                        },
                    },
                    "required": ["content", "status"],
                },
                "description": "List of todo items",
            },
        },
        "required": ["todos"],
    }

    async def execute(self, input: dict, cwd: Path) -> ToolResult:
        todos = input["todos"]
        return ToolResult(content=json.dumps(todos))
