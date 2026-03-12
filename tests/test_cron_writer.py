"""Tests for agentpod.cron.writer."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentpod.cron.writer import (
    compute_min_interval,
    create_cron_task,
    delete_cron_task_files,
    generate_task_md,
    update_cron_task,
    validate_schedule,
    validate_task_name,
)
from agentpod.skills import load_frontmatter_and_body


# ── validate_task_name ────────────────────────────────────────────

def test_valid_names():
    for name in ["a", "daily-report", "task1", "a-b-c", "x" * 64]:
        validate_task_name(name)  # should not raise


def test_empty_name():
    with pytest.raises(ValueError, match="cannot be empty"):
        validate_task_name("")


def test_name_too_long():
    with pytest.raises(ValueError, match="too long"):
        validate_task_name("x" * 65)


def test_invalid_name_uppercase():
    with pytest.raises(ValueError, match="Invalid task name"):
        validate_task_name("Daily-Report")


def test_invalid_name_special_chars():
    with pytest.raises(ValueError, match="Invalid task name"):
        validate_task_name("my_task")


def test_invalid_name_leading_hyphen():
    with pytest.raises(ValueError, match="Invalid task name"):
        validate_task_name("-task")


def test_invalid_name_trailing_hyphen():
    with pytest.raises(ValueError, match="Invalid task name"):
        validate_task_name("task-")


# ── validate_schedule ─────────────────────────────────────────────

def test_valid_schedule():
    validate_schedule("0 9 * * *")  # should not raise


def test_invalid_schedule():
    with pytest.raises(ValueError, match="Invalid cron expression"):
        validate_schedule("not a cron")


def test_schedule_below_min_interval():
    with pytest.raises(ValueError, match="below minimum"):
        validate_schedule("* * * * *", min_interval=3600)  # every minute < 1h


def test_schedule_meets_min_interval():
    validate_schedule("0 * * * *", min_interval=3600)  # hourly == 1h, ok


# ── compute_min_interval ─────────────────────────────────────────

def test_interval_every_minute():
    assert compute_min_interval("* * * * *") == 60.0


def test_interval_hourly():
    assert compute_min_interval("0 * * * *") == 3600.0


def test_interval_daily():
    assert compute_min_interval("0 9 * * *") == 86400.0


# ── generate_task_md ──────────────────────────────────────────────

def test_generate_minimal():
    md = generate_task_md(
        name="test", description="A test", schedule="0 9 * * *",
        prompt="Do something.",
    )
    assert md.startswith("---\n")
    assert "name: test" in md
    assert 'schedule: "0 9 * * *"' in md
    assert "Do something." in md
    # Defaults should NOT appear in output
    assert "timezone" not in md
    assert "enabled" not in md
    assert "timeout" not in md
    assert "max_turns" not in md
    assert "model" not in md


def test_generate_all_fields():
    md = generate_task_md(
        name="full", description="Full test", schedule="*/5 * * * *",
        prompt="Run it.", timezone="UTC", enabled=False, timeout=300,
        max_turns=50, model="gpt-4",
    )
    assert "timezone: UTC" in md
    assert "enabled: false" in md
    assert "timeout: 300" in md
    assert "max_turns: 50" in md
    assert "model: gpt-4" in md


def test_generate_roundtrip(tmp_path: Path):
    """Generated TASK.md can be parsed back by load_frontmatter_and_body."""
    md = generate_task_md(
        name="roundtrip", description="测试中文", schedule="0 9 * * *",
        prompt="请执行任务。",
    )
    task_md = tmp_path / "TASK.md"
    task_md.write_text(md, encoding="utf-8")
    meta, body = load_frontmatter_and_body(task_md)
    assert meta["name"] == "roundtrip"
    assert meta["description"] == "测试中文"
    assert meta["schedule"] == "0 9 * * *"
    assert body.strip() == "请执行任务。"


# ── create_cron_task ──────────────────────────────────────────────

def test_create_basic(tmp_path: Path):
    create_cron_task(
        str(tmp_path), name="my-task", description="Test",
        schedule="0 9 * * *", prompt="Hello.",
    )
    task_md = tmp_path / ".agents" / "cron" / "my-task" / "TASK.md"
    assert task_md.is_file()
    meta, body = load_frontmatter_and_body(task_md)
    assert meta["name"] == "my-task"
    assert body.strip() == "Hello."


def test_create_already_exists(tmp_path: Path):
    create_cron_task(
        str(tmp_path), name="dup", description="Test",
        schedule="0 9 * * *", prompt="Hello.",
    )
    with pytest.raises(FileExistsError):
        create_cron_task(
            str(tmp_path), name="dup", description="Test2",
            schedule="0 10 * * *", prompt="Again.",
        )


def test_create_invalid_name(tmp_path: Path):
    with pytest.raises(ValueError):
        create_cron_task(
            str(tmp_path), name="BAD_NAME", description="Test",
            schedule="0 9 * * *", prompt="Hello.",
        )


def test_create_min_interval_rejected(tmp_path: Path):
    with pytest.raises(ValueError, match="below minimum"):
        create_cron_task(
            str(tmp_path), name="fast", description="Test",
            schedule="* * * * *", prompt="Hello.", min_interval=3600,
        )


# ── update_cron_task ──────────────────────────────────────────────

def test_update_partial(tmp_path: Path):
    create_cron_task(
        str(tmp_path), name="upd", description="Original",
        schedule="0 9 * * *", prompt="Old prompt.",
    )
    update_cron_task(str(tmp_path), "upd", description="Updated")
    meta, body = load_frontmatter_and_body(
        tmp_path / ".agents" / "cron" / "upd" / "TASK.md"
    )
    assert meta["description"] == "Updated"
    assert meta["schedule"] == "0 9 * * *"  # unchanged
    assert body.strip() == "Old prompt."  # unchanged


def test_update_not_found(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        update_cron_task(str(tmp_path), "nonexistent", description="X")


def test_update_schedule_with_min_interval(tmp_path: Path):
    create_cron_task(
        str(tmp_path), name="sched", description="Test",
        schedule="0 9 * * *", prompt="Hello.",
    )
    with pytest.raises(ValueError, match="below minimum"):
        update_cron_task(
            str(tmp_path), "sched", schedule="* * * * *", min_interval=3600,
        )


# ── delete_cron_task_files ────────────────────────────────────────

def test_delete_files(tmp_path: Path):
    create_cron_task(
        str(tmp_path), name="del-me", description="Test",
        schedule="0 9 * * *", prompt="Hello.",
    )
    task_dir = tmp_path / ".agents" / "cron" / "del-me"
    assert task_dir.is_dir()
    delete_cron_task_files(str(tmp_path), "del-me")
    assert not task_dir.exists()


def test_delete_not_found(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        delete_cron_task_files(str(tmp_path), "ghost")
