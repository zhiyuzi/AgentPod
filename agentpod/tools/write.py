"""WriteTool - atomic file write."""

from __future__ import annotations

import tempfile
from pathlib import Path

from agentpod.tools.base import Tool, ToolResult, safe_resolve


class WriteTool(Tool):
    name = "write"
    description = "Write content to a file. Creates parent directories if needed."
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to the file to write"},
            "content": {"type": "string", "description": "Content to write"},
        },
        "required": ["file_path", "content"],
    }

    async def execute(self, input: dict, cwd: Path) -> ToolResult:
        try:
            resolved = safe_resolve(input["file_path"], cwd)
        except PermissionError as e:
            return ToolResult(content=str(e), is_error=True)

        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)

            # Atomic write: write to temp file then rename
            fd, tmp_path = tempfile.mkstemp(
                dir=str(resolved.parent), suffix=".tmp"
            )
            try:
                with open(fd, "w", encoding="utf-8") as f:
                    f.write(input["content"])
                Path(tmp_path).replace(resolved)
            except Exception:
                Path(tmp_path).unlink(missing_ok=True)
                raise

            return ToolResult(content=f"Wrote {len(input['content'])} bytes to {input['file_path']}")
        except Exception as e:
            return ToolResult(content=f"Error writing file: {e}", is_error=True)
