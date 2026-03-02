"""Tests for SessionManager."""

from pathlib import Path

import pytest

from agentpod.runtime.session import SessionManager


@pytest.fixture
def session_mgr(tmp_path: Path) -> SessionManager:
    return SessionManager(tmp_path / "sessions")


def test_create_append_load(session_mgr: SessionManager):
    sid = session_mgr.create()
    assert len(sid) == 12

    session_mgr.append(sid, {"role": "user", "content": "hello"})
    session_mgr.append(sid, {"role": "assistant", "content": "hi"})
    session_mgr.append(sid, {"role": "user", "content": "bye"})

    messages = session_mgr.load(sid)
    assert len(messages) == 3
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "hello"
    assert messages[1]["role"] == "assistant"
    assert messages[2]["content"] == "bye"


def test_list_sessions(session_mgr: SessionManager):
    sid1 = session_mgr.create()
    sid2 = session_mgr.create()

    sessions = session_mgr.list()
    session_ids = [s.session_id for s in sessions]
    assert sid1 in session_ids
    assert sid2 in session_ids


def test_fork(session_mgr: SessionManager):
    sid = session_mgr.create()
    session_mgr.append(sid, {"role": "user", "content": "msg1"})
    session_mgr.append(sid, {"role": "assistant", "content": "msg2"})

    new_sid = session_mgr.fork(sid)
    assert new_sid != sid

    # Forked session has same messages
    messages = session_mgr.load(new_sid)
    assert len(messages) == 2
    assert messages[0]["content"] == "msg1"

    # Forked session has parent_session_id
    meta = session_mgr.get_meta(new_sid)
    assert meta.parent_session_id == sid


def test_get_meta(session_mgr: SessionManager):
    sid = session_mgr.create()
    meta = session_mgr.get_meta(sid)
    assert meta.session_id == sid
    assert meta.created_at is not None
    assert meta.parent_session_id is None


def test_load_nonexistent(session_mgr: SessionManager):
    with pytest.raises(FileNotFoundError):
        session_mgr.load("nonexistent")


def test_create_with_id(session_mgr: SessionManager):
    sid = session_mgr.create_with_id("cron_daily_20260228_090000")
    assert sid == "cron_daily_20260228_090000"
    meta = session_mgr.get_meta(sid)
    assert meta.session_id == "cron_daily_20260228_090000"
    assert meta.created_at is not None


def test_create_with_id_source(session_mgr: SessionManager):
    sid = session_mgr.create_with_id("cron_test_123", source="cron")
    import json

    path = session_mgr._path(sid)
    first_line = path.read_text(encoding="utf-8").split("\n", 1)[0]
    meta_dict = json.loads(first_line)
    assert meta_dict["source"] == "cron"


def test_create_default_source(session_mgr: SessionManager):
    sid = session_mgr.create()
    import json

    path = session_mgr._path(sid)
    first_line = path.read_text(encoding="utf-8").split("\n", 1)[0]
    meta_dict = json.loads(first_line)
    assert meta_dict["source"] == "interactive"


def test_create_with_id_append_load(session_mgr: SessionManager):
    sid = session_mgr.create_with_id("custom_session_id")
    session_mgr.append(sid, {"role": "user", "content": "hello"})
    messages = session_mgr.load(sid)
    assert len(messages) == 1
    assert messages[0]["content"] == "hello"
