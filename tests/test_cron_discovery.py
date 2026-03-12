"""Tests for agentpod.cron.discovery."""

from __future__ import annotations

from pathlib import Path

from agentpod.cron.discovery import discover_cron_tasks


def _write_task_md(task_dir: Path, frontmatter: str, body: str = "") -> None:
    task_dir.mkdir(parents=True, exist_ok=True)
    content = f"---\n{frontmatter}---\n\n{body}\n"
    (task_dir / "TASK.md").write_text(content, encoding="utf-8")


# ── valid: all fields ──────────────────────────────────────────────

def test_valid_all_fields(tmp_path: Path):
    fm = (
        'name: daily-report\n'
        'description: 生成每日数据汇总报告\n'
        'schedule: "0 9 * * *"\n'
        'timezone: UTC\n'
        'enabled: false\n'
        'timeout: 300\n'
        'max_turns: 20\n'
        'model: doubao-seed-1-8-251228\n'
    )
    body = "请分析今天的数据变化，生成一份简洁的日报。"
    _write_task_md(tmp_path / "daily-report", fm, body)

    tasks = discover_cron_tasks(tmp_path)
    assert len(tasks) == 1
    t = tasks[0]
    assert t["name"] == "daily-report"
    assert t["description"] == "生成每日数据汇总报告"
    assert t["schedule"] == "0 9 * * *"
    assert t["timezone"] == "UTC"
    assert t["enabled"] is False
    assert t["timeout"] == 300
    assert t["max_turns"] == 20
    assert t["model"] == "doubao-seed-1-8-251228"
    assert t["prompt"] == body
    assert t["content_hash"]  # non-empty hash of entire file
    assert t["dir"] == tmp_path / "daily-report"


# ── valid: only required fields → defaults applied ─────────────────

def test_defaults_applied(tmp_path: Path):
    fm = (
        'name: cleanup\n'
        'description: 清理临时文件\n'
        'schedule: "0 3 * * *"\n'
    )
    body = "清理所有超过 7 天的临时文件。"
    _write_task_md(tmp_path / "cleanup", fm, body)

    tasks = discover_cron_tasks(tmp_path)
    assert len(tasks) == 1
    t = tasks[0]
    assert t["timezone"] == "Asia/Shanghai"
    assert t["enabled"] is True
    assert t["timeout"] == 1200
    assert t["max_turns"] == 0
    assert t["model"] == ""


# ── missing required field → skipped ───────────────────────────────

def test_missing_name(tmp_path: Path, caplog):
    fm = 'description: test\nschedule: "* * * * *"\n'
    _write_task_md(tmp_path / "no-name", fm, "body")

    tasks = discover_cron_tasks(tmp_path)
    assert tasks == []
    assert "missing required" in caplog.text


def test_missing_description(tmp_path: Path, caplog):
    fm = 'name: nodesc\nschedule: "* * * * *"\n'
    _write_task_md(tmp_path / "nodesc", fm, "body")

    tasks = discover_cron_tasks(tmp_path)
    assert tasks == []
    assert "missing required" in caplog.text


def test_missing_schedule(tmp_path: Path, caplog):
    fm = 'name: nosched\ndescription: test\n'
    _write_task_md(tmp_path / "nosched", fm, "body")

    tasks = discover_cron_tasks(tmp_path)
    assert tasks == []
    assert "missing required" in caplog.text


# ── name mismatch → skipped ────────────────────────────────────────

def test_name_mismatch(tmp_path: Path, caplog):
    fm = 'name: wrong-name\ndescription: test\nschedule: "* * * * *"\n'
    _write_task_md(tmp_path / "actual-dir", fm, "body")

    tasks = discover_cron_tasks(tmp_path)
    assert tasks == []
    assert "does not match directory name" in caplog.text


# ── invalid cron expression → skipped ──────────────────────────────

def test_invalid_cron(tmp_path: Path, caplog):
    fm = 'name: badcron\ndescription: test\nschedule: "not a cron"\n'
    _write_task_md(tmp_path / "badcron", fm, "body")

    tasks = discover_cron_tasks(tmp_path)
    assert tasks == []
    assert "invalid cron expression" in caplog.text


# ── empty dir / no TASK.md → skipped ──────────────────────────────

def test_empty_dir(tmp_path: Path):
    (tmp_path / "empty-dir").mkdir()
    tasks = discover_cron_tasks(tmp_path)
    assert tasks == []


def test_nonexistent_dir(tmp_path: Path):
    tasks = discover_cron_tasks(tmp_path / "does-not-exist")
    assert tasks == []


# ── multiple tasks → sorted by directory name ─────────────────────

def test_multiple_sorted(tmp_path: Path):
    for name in ("zeta-task", "alpha-task", "mid-task"):
        fm = f'name: {name}\ndescription: desc\nschedule: "* * * * *"\n'
        _write_task_md(tmp_path / name, fm, f"prompt for {name}")

    tasks = discover_cron_tasks(tmp_path)
    assert len(tasks) == 3
    assert [t["name"] for t in tasks] == ["alpha-task", "mid-task", "zeta-task"]


# ── enabled=false preserved ───────────────────────────────────────

def test_enabled_false_preserved(tmp_path: Path):
    fm = (
        'name: disabled-task\n'
        'description: should be disabled\n'
        'schedule: "0 0 * * *"\n'
        'enabled: false\n'
    )
    _write_task_md(tmp_path / "disabled-task", fm, "body")

    tasks = discover_cron_tasks(tmp_path)
    assert len(tasks) == 1
    assert tasks[0]["enabled"] is False
