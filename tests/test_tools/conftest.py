"""Shared fixtures for tool tests."""

import pytest
from pathlib import Path


@pytest.fixture
def tmp_cwd(tmp_path):
    """Create a CWD structure for tool tests (with Agent Skills spec-compliant SKILL.md)."""
    (tmp_path / "AGENTS.md").write_text("# Test Agent")

    skills_dir = tmp_path / ".agents" / "skills" / "hello"
    skills_dir.mkdir(parents=True)
    (skills_dir / "SKILL.md").write_text(
        "---\n"
        "name: hello\n"
        "description: A test skill that outputs hello.\n"
        "---\n"
        "\nRun `scripts/run.sh` to output hello.\n"
    )

    scripts_dir = skills_dir / "scripts"
    scripts_dir.mkdir()
    (scripts_dir / "run.sh").write_text("#!/bin/bash\necho hello")

    (tmp_path / "sessions").mkdir()
    (tmp_path / "test.txt").write_text("line 1\nline 2\nline 3\nline 4\nline 5\n")

    return tmp_path
