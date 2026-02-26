"""GrepTool - search file contents with regex."""

from __future__ import annotations

import re
from pathlib import Path

from agentpod.tools.base import Tool, ToolResult, safe_resolve


class GrepTool(Tool):
    name = "grep"
    description = "Search file contents using regex patterns."
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "Regex pattern to search for"},
            "path": {"type": "string", "description": "File or directory to search in (relative to cwd)"},
            "include": {"type": "string", "description": "Glob pattern to filter files (e.g. '*.py')"},
            "context": {"type": "integer", "description": "Number of context lines before and after each match"},
        },
        "required": ["pattern"],
    }

    async def execute(self, input: dict, cwd: Path) -> ToolResult:
        search_path = cwd
        if "path" in input and input["path"]:
            try:
                search_path = safe_resolve(input["path"], cwd)
            except PermissionError as e:
                return ToolResult(content=str(e), is_error=True)

        try:
            regex = re.compile(input["pattern"])
        except re.error as e:
            return ToolResult(content=f"Invalid regex: {e}", is_error=True)

        include = input.get("include", "*")
        ctx = input.get("context", 0)

        results: list[str] = []

        if search_path.is_file():
            files = [search_path]
        else:
            files = sorted(search_path.rglob(include))

        for fpath in files:
            if not fpath.is_file():
                continue
            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue

            lines = text.splitlines()
            matched_indices: set[int] = set()

            for i, line in enumerate(lines):
                if regex.search(line):
                    matched_indices.add(i)

            if not matched_indices:
                continue

            # Expand with context
            display_indices: set[int] = set()
            for idx in matched_indices:
                for c in range(max(0, idx - ctx), min(len(lines), idx + ctx + 1)):
                    display_indices.add(c)

            rel = fpath.relative_to(cwd) if str(fpath).startswith(str(cwd)) else fpath
            for idx in sorted(display_indices):
                prefix = ":" if idx in matched_indices else "-"
                results.append(f"{rel}{prefix}{idx + 1}{prefix}{lines[idx]}")

        if not results:
            return ToolResult(content="No matches found.")

        return ToolResult(content="\n".join(results))
