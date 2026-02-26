"""WebSearchTool - placeholder for web search."""

from __future__ import annotations

from pathlib import Path

from agentpod.tools.base import Tool, ToolResult


class WebSearchTool(Tool):
    name = "web_search"
    description = "Search the web for information."
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
        },
        "required": ["query"],
    }

    async def execute(self, input: dict, cwd: Path) -> ToolResult:
        return ToolResult(content="Web search is not configured")
