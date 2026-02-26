"""Shared fixtures for tool tests."""

import pytest
from pathlib import Path


@pytest.fixture
def tmp_cwd(tmp_path):
    """Create a CWD structure for tool tests."""
    (tmp_path / "AGENTS.md").write_text("# Test Agent")

    skills_dir = tmp_path / ".agents" / "skills" / "hello"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text("Hello skill - a test skill\n\nDetails here.")

    scripts_dir = skills_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "run.sh").write_text("#!/bin/bash\necho hello")

    (tmp_path / "sessions").mkdir()
    (tmp_path / "test.txt").write_text("line 1\nline 2\nline 3\nline 4\nline 5\n")

    return tmp_path
