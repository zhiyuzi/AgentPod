"""Prompt management: runtime preamble + AGENTS.md from the working directory."""

from __future__ import annotations

from pathlib import Path

# ---------------------------------------------------------------------------
# Runtime Preamble — business-agnostic behavioral instructions injected
# before every AGENTS.md.  These are engineering-level norms that ensure
# consistent agent behavior regardless of the downstream business scenario.
# ---------------------------------------------------------------------------
RUNTIME_PREAMBLE = """\
# Tool-Use Norms

- When you call tools, always accompany the call with a brief explanatory \
text that tells the user what you are doing and why. Never call tools silently.
- When multiple tool calls are independent of each other, invoke them in the \
same turn to minimise unnecessary round-trips.
- For complex, multi-step tasks, report progress incrementally as you go \
rather than waiting until everything is finished.
- If a tool returns an error, analyse the cause and try an alternative \
approach instead of retrying the exact same operation.
"""


class PromptManager:
    def __init__(self, cwd: Path):
        self.cwd = cwd
        self._content: str | None = None

    def load(self) -> str:
        path = self.cwd / "AGENTS.md"
        if not path.exists():
            raise FileNotFoundError(f"AGENTS.md not found in {self.cwd}")
        agents_md = path.read_text(encoding="utf-8")
        self._content = RUNTIME_PREAMBLE + "\n" + agents_md
        return self._content

    def reload(self) -> str:
        self._content = None
        return self.load()
