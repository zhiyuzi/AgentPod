"""AskUserTool - request input from the user."""

from __future__ import annotations

import json
from pathlib import Path

from agentpod.tools.base import Tool, ToolResult


class AskUserTool(Tool):
    name = "ask_user"
    description = "Ask the user a question and optionally provide choices."
    input_schema = {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "Question to ask the user"},
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional list of choices",
            },
        },
        "required": ["question"],
    }

    async def execute(self, input: dict, cwd: Path) -> ToolResult:
        payload = {
            "type": "ask_user",
            "question": input["question"],
            "options": input.get("options", []),
        }
        return ToolResult(content=json.dumps(payload, ensure_ascii=False))
