"""TodoWriteTool - manage a structured task list."""

from __future__ import annotations

import json
from pathlib import Path

from agentpod.tools.base import Tool, ToolResult


class TodoWriteTool(Tool):
    name = "todo_write"
    description = (
        "Create or update a structured todo list to track progress on "
        "multi-step tasks. Use this when a task requires 3 or more distinct "
        "steps — it helps you stay organised and shows the user your progress. "
        "Do NOT use this for single-step or trivial tasks. "
        "Keep exactly one item in_progress at a time; mark each item "
        "completed as soon as you finish it, before moving to the next."
    )
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
        return ToolResult(content=json.dumps(todos, ensure_ascii=False))
