"""Base classes and utilities for the tool layer."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ToolResult:
    content: str
    is_error: bool = False


class Tool(ABC):
    name: str
    description: str
    input_schema: dict  # JSON Schema

    @abstractmethod
    async def execute(self, input: dict, cwd: Path) -> ToolResult: ...


def safe_resolve(path: str | Path, cwd: Path) -> Path:
    """Resolve path to absolute, verify it's within cwd. Raise PermissionError if not."""
    resolved = (cwd / path).resolve()
    cwd_resolved = cwd.resolve()
    if not str(resolved).startswith(str(cwd_resolved)):
        raise PermissionError(f"Path {path} is outside CWD boundary")
    return resolved
