"""Prompt management: loads AGENTS.md from the working directory."""

from __future__ import annotations

from pathlib import Path


class PromptManager:
    def __init__(self, cwd: Path):
        self.cwd = cwd
        self._content: str | None = None

    def load(self) -> str:
        path = self.cwd / "AGENTS.md"
        if not path.exists():
            raise FileNotFoundError(f"AGENTS.md not found in {self.cwd}")
        self._content = path.read_text(encoding="utf-8")
        return self._content

    def reload(self) -> str:
        self._content = None
        return self.load()
