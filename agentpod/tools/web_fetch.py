"""WebFetchTool - fetch and extract text from a URL."""

from __future__ import annotations

import re
from pathlib import Path

import httpx

from agentpod.tools.base import Tool, ToolResult

_MAX_CONTENT_LENGTH = 50_000


class WebFetchTool(Tool):
    name = "web_fetch"
    description = "Fetch a URL and return its text content."
    input_schema = {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "URL to fetch"},
        },
        "required": ["url"],
    }

    async def execute(self, input: dict, cwd: Path) -> ToolResult:
        url = input["url"]
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except Exception as e:
            return ToolResult(content=f"Fetch error: {e}", is_error=True)

        html = resp.text
        # Strip HTML tags
        text = re.sub(r"<[^>]+>", "", html)
        # Collapse whitespace
        text = re.sub(r"\s+", " ", text).strip()

        if len(text) > _MAX_CONTENT_LENGTH:
            text = text[:_MAX_CONTENT_LENGTH] + "\n\n[Content truncated]"

        return ToolResult(content=text)
