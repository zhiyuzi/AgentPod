"""Session management via JSONL files."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from agentpod.types import SessionMeta


class SessionManager:
    def __init__(self, sessions_dir: Path):
        self.sessions_dir = sessions_dir
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.jsonl"

    def create(self) -> str:
        session_id = uuid.uuid4().hex[:12]
        meta = {
            "type": "meta",
            "session_id": session_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "parent_session_id": None,
        }
        path = self._path(session_id)
        path.write_text(json.dumps(meta, ensure_ascii=False) + "\n", encoding="utf-8")
        return session_id

    def load(self, session_id: str) -> list[dict]:
        path = self._path(session_id)
        if not path.exists():
            raise FileNotFoundError(f"Session {session_id} not found")
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        # Skip first line (meta)
        messages = []
        for line in lines[1:]:
            if line.strip():
                messages.append(json.loads(line))
        return messages

    def append(self, session_id: str, message: dict):
        path = self._path(session_id)
        if not path.exists():
            raise FileNotFoundError(f"Session {session_id} not found")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(message, ensure_ascii=False) + "\n")

    def list(self) -> list[SessionMeta]:
        sessions: list[tuple[float, SessionMeta]] = []
        for p in self.sessions_dir.glob("*.jsonl"):
            try:
                first_line = p.read_text(encoding="utf-8").split("\n", 1)[0]
                meta_dict = json.loads(first_line)
                meta = SessionMeta(
                    session_id=meta_dict["session_id"],
                    created_at=meta_dict["created_at"],
                    parent_session_id=meta_dict.get("parent_session_id"),
                )
                sessions.append((p.stat().st_mtime, meta))
            except (json.JSONDecodeError, KeyError):
                continue
        sessions.sort(key=lambda x: x[0], reverse=True)
        return [s[1] for s in sessions]

    def fork(self, session_id: str) -> str:
        messages = self.load(session_id)
        old_meta = self.get_meta(session_id)
        new_id = self.create()
        # Rewrite meta with parent_session_id
        path = self._path(new_id)
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        meta_dict = json.loads(lines[0])
        meta_dict["parent_session_id"] = session_id
        path.write_text(json.dumps(meta_dict, ensure_ascii=False) + "\n", encoding="utf-8")
        # Append all messages from original session
        for msg in messages:
            self.append(new_id, msg)
        return new_id

    def get_meta(self, session_id: str) -> SessionMeta:
        path = self._path(session_id)
        if not path.exists():
            raise FileNotFoundError(f"Session {session_id} not found")
        first_line = path.read_text(encoding="utf-8").split("\n", 1)[0]
        meta_dict = json.loads(first_line)
        return SessionMeta(
            session_id=meta_dict["session_id"],
            created_at=meta_dict["created_at"],
            parent_session_id=meta_dict.get("parent_session_id"),
        )
