"""Tests for Alembic initialization and batch-mode migration configuration."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Protocol, runtime_checkable

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_ENV = PROJECT_ROOT / "alembic" / "env.py"
BATCH_MODE_OCCURRENCES_REQUIRED = 2


def test_alembic_upgrade_head_works_on_empty_db(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure `alembic upgrade head` succeeds on an empty SQLite database."""
    db_path = tmp_path / "alembic-empty.sqlite3"
    monkeypatch_proxy = _as_monkeypatch(monkeypatch)
    monkeypatch_proxy.setenv("TCA_DB_PATH", db_path.as_posix())

    result = _run_alembic_command(("upgrade", "head"), db_path)
    if result.returncode != 0:
        raise AssertionError

    if not db_path.exists():
        raise AssertionError


def test_alembic_batch_mode_is_enabled_in_env_configuration() -> None:
    """Ensure Alembic env explicitly enables SQLite render_as_batch mode."""
    env_text = ALEMBIC_ENV.read_text(encoding="utf-8")
    if env_text.count("render_as_batch=True") < BATCH_MODE_OCCURRENCES_REQUIRED:
        raise AssertionError


def test_alembic_current_command_invokable_from_project_root(
    tmp_path: Path,
) -> None:
    """Ensure Alembic CLI commands can run from repository root."""
    db_path = tmp_path / "alembic-current.sqlite3"
    result = _run_alembic_command(("current",), db_path)
    if result.returncode != 0:
        raise AssertionError


def _as_monkeypatch(value: object) -> MonkeyPatchLike:
    """Narrow monkeypatch fixture object to setenv-capable helper."""
    if not isinstance(value, MonkeyPatchLike):
        raise TypeError
    return value


@runtime_checkable
class MonkeyPatchLike(Protocol):
    """Runtime-checkable subset of pytest monkeypatch fixture behavior."""

    def setenv(self, name: str, value: str) -> None:
        """Set environment variable for duration of current test."""


def _run_alembic_command(
    command_parts: tuple[str, ...],
    db_path: Path,
) -> subprocess.CompletedProcess[str]:
    """Execute alembic CLI command from repository root with explicit DB path."""
    alembic_executable = Path(sys.executable).with_name("alembic")
    if not alembic_executable.exists():
        raise AssertionError

    env = os.environ.copy()
    env["TCA_DB_PATH"] = db_path.as_posix()
    return subprocess.run(  # noqa: S603
        [alembic_executable.as_posix(), "-c", "alembic.ini", *command_parts],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
