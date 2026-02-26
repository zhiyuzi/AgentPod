"""GlobTool - file pattern matching."""

from __future__ import annotations

from pathlib import Path

from agentpod.tools.base import Tool, ToolResult, safe_resolve


class GlobTool(Tool):
    name = "glob"
    description = "Find files matching a glob pattern within the working directory."
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Glob pattern (e.g. '*.py', '**/*.md')"},
            "path": {"type": "string", "description": "Subdirectory to search in (relative to cwd)"},
        },
        "required": ["pattern"],
    }

    async def execute(self, input: dict, cwd: Path) -> ToolResult:
        search_dir = cwd
        if "path" in input and input["path"]:
            try:
                search_dir = safe_resolve(input["path"], cwd)
            except PermissionError as e:
                return ToolResult(content=str(e), is_error=True)

        pattern = input["pattern"]
        try:
            matches = sorted(
                str(p.relative_to(cwd))
                for p in search_dir.glob(pattern)
                if p.is_file()
            )
        except Exception as e:
            return ToolResult(content=f"Glob error: {e}", is_error=True)

        if not matches:
            return ToolResult(content="No files matched the pattern.")

        return ToolResult(content="\n".join(matches))
