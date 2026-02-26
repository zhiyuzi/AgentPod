"""ReadTool - read file contents with line numbers."""

from __future__ import annotations

from pathlib import Path

from agentpod.tools.base import Tool, ToolResult, safe_resolve


class ReadTool(Tool):
    name = "read"
    description = "Read a file and return its content with line numbers."
    input_schema = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string", "description": "Path to the file to read"},
            "offset": {"type": "integer", "description": "Start line (1-based)"},
            "limit": {"type": "integer", "description": "Maximum number of lines to return"},
        },
        "required": ["file_path"],
    }

    async def execute(self, input: dict, cwd: Path) -> ToolResult:
        try:
            resolved = safe_resolve(input["file_path"], cwd)
        except PermissionError as e:
            return ToolResult(content=str(e), is_error=True)

        if not resolved.is_file():
            return ToolResult(content=f"File not found: {input['file_path']}", is_error=True)

        try:
            text = resolved.read_text(encoding="utf-8")
        except Exception as e:
            return ToolResult(content=f"Error reading file: {e}", is_error=True)

        lines = text.splitlines(keepends=True)
        offset = input.get("offset", 1)
        limit = input.get("limit")

        # offset is 1-based
        start = max(offset - 1, 0)
        end = start + limit if limit else len(lines)
        selected = lines[start:end]

        numbered = []
        for i, line in enumerate(selected, start=start + 1):
            numbered.append(f"{i:>6}\t{line.rstrip()}")

        return ToolResult(content="\n".join(numbered))
