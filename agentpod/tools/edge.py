"""Edge tool proxy — bridges Edge Agent tools into the ToolRegistry."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from agentpod.edge import EdgeConnection
from agentpod.tools.base import Tool, ToolResult

_log = logging.getLogger("agentpod.tools.edge")


class EdgeTool(Tool):
    """A dynamically created tool that proxies execution to an Edge Agent."""

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict,
        connection: EdgeConnection,
    ):
        self.name = f"edge_{name}"
        self.description = f"[Edge] {description}"
        self.input_schema = input_schema
        self._connection = connection
        self._raw_name = name  # original name for the Edge Agent

    async def execute(self, input: dict, cwd: Path) -> ToolResult:
        try:
            response = await self._connection.request({
                "type": "tools_call",
                "tool_name": self._raw_name,
                "input": input,
            })
            return ToolResult(
                content=response.get("content", ""),
                is_error=response.get("is_error", False),
            )
        except Exception as e:
            return ToolResult(content=f"Edge tool error: {e}", is_error=True)


async def discover_edge_tools(
    connection: EdgeConnection,
    edge_config: dict[str, dict],
) -> list[EdgeTool]:
    """Ask the Edge Agent for its tools list, filter by config, return EdgeTool instances."""
    try:
        response = await connection.request({"type": "tools_list"})
        tools_data = response.get("tools", [])
    except Exception as e:
        _log.warning("Failed to discover Edge tools: %s", e)
        return []

    results: list[EdgeTool] = []
    for t in tools_data:
        name = t.get("name", "")
        if not name:
            continue
        # If config exists, check enabled status; if no config, allow all
        if edge_config:
            cfg = edge_config.get(name)
            if cfg is not None and not cfg.get("enabled", True):
                continue
        results.append(EdgeTool(
            name=name,
            description=t.get("description", ""),
            input_schema=t.get("input_schema", {}),
            connection=connection,
        ))
    return results


def load_edge_config(shared_dir: Path | None) -> dict[str, dict[str, Any]]:
    """Load Edge config from shared_dir/.agents/config.toml.

    Returns {name: {enabled, description, ...}} dict.
    Empty dict if file doesn't exist (= allow all tools).
    """
    if not shared_dir:
        return {}
    config_path = shared_dir / ".agents" / "config.toml"
    if not config_path.is_file():
        return {}

    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]

    with open(config_path, "rb") as f:
        data = tomllib.load(f)

    result: dict[str, dict[str, Any]] = {}
    for entry in data.get("edge", []):
        name = entry.get("name", "")
        if name:
            result[name] = entry
    return result
