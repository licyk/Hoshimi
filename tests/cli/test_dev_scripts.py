"""The developer task runner imported from Hanaikada and adapted to Hoshimi."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest


def run_script(path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(path), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )


def test_dev_task_runner_lists_the_debug_build_and_test_tasks() -> None:
    result = run_script(Path("scripts/dev.py"))

    assert result.returncode == 0
    for task in ("dev", "test-py", "test-web", "check", "wheel"):
        assert task in result.stdout


def test_dev_task_runner_rejects_an_unknown_task_without_starting_a_tool() -> None:
    result = run_script(Path("scripts/dev.py"), "definitely-not-a-task")

    assert result.returncode == 2
    assert "unknown task" in result.stderr


def test_build_wheel_script_has_a_help_surface() -> None:
    result = run_script(Path("scripts/build_wheel.py"), "--help")

    assert result.returncode == 0
    assert "--keep-web-dist" in result.stdout


@pytest.mark.parametrize("task", ["lint", "test-py", "test-web", "check", "wheel"])
def test_dev_runner_registers_the_expected_groups(task: str) -> None:
    result = run_script(Path("scripts/dev.py"))

    assert f" {task:<14}" in result.stdout
