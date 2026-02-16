"""Tests for startup migration execution in app lifespan (C018)."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from http import HTTPStatus
from pathlib import Path
from typing import Protocol, cast, runtime_checkable
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from tca.api.app import create_app
from tca.storage import MigrationStartupError

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_EXECUTABLE = Path(sys.executable).with_name("alembic")


def test_startup_migrations_upgrade_empty_db_to_head(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure startup upgrades an empty SQLite DB to Alembic head."""
    db_path = tmp_path / "startup-empty.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (tmp_path / "startup-bootstrap-token.txt").as_posix(),
    )

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/health")
        if response.status_code != HTTPStatus.OK:
            raise AssertionError

    if not db_path.exists():
        raise AssertionError

    versions = _read_alembic_versions(db_path)
    if len(versions) != 1:
        raise AssertionError

    if not _db_revision_is_head(db_path):
        raise AssertionError


def test_startup_migrations_are_idempotent_on_current_db(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure startup migration runner is safe to execute repeatedly."""
    db_path = tmp_path / "startup-idempotent.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (tmp_path / "startup-bootstrap-token.txt").as_posix(),
    )

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/health")
        if response.status_code != HTTPStatus.OK:
            raise AssertionError

    initial_versions = _read_alembic_versions(db_path)

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/health")
        if response.status_code != HTTPStatus.OK:
            raise AssertionError

    repeated_versions = _read_alembic_versions(db_path)
    if len(initial_versions) != 1:
        raise AssertionError
    if len(repeated_versions) != 1:
        raise AssertionError
    if initial_versions != repeated_versions:
        raise AssertionError
    if not _db_revision_is_head(db_path):
        raise AssertionError


def test_startup_migration_failure_blocks_api_startup(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure API startup fails before serving requests when migrations fail."""
    db_path = tmp_path / "startup-failure.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (tmp_path / "startup-bootstrap-token.txt").as_posix(),
    )

    failed_upgrade = subprocess.CompletedProcess(
        args=["alembic", "-c", "alembic.ini", "upgrade", "head"],
        returncode=1,
        stdout="",
        stderr="forced-migration-failure",
    )
    with patch(
        "tca.storage.migrations.subprocess.run",
        return_value=failed_upgrade,
    ):
        app = create_app()
        with (
            pytest.raises(
                MigrationStartupError,
                match=r"forced-migration-failure",
            ),
            TestClient(app),
        ):
            pass


def test_startup_migration_path_prepare_failure_raises_domain_error(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure filesystem prep failures surface as startup migration errors."""
    db_path = tmp_path / "startup-path-prepare-failure.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (tmp_path / "startup-bootstrap-token.txt").as_posix(),
    )

    with patch(
        "tca.storage.migrations.Path.mkdir",
        side_effect=PermissionError("forced-path-permission-denied"),
    ):
        app = create_app()
        with (
            pytest.raises(
                MigrationStartupError,
                match=r"Failed to prepare database path.*forced-path-permission-denied",
            ),
            TestClient(app),
        ):
            pass


def _read_alembic_versions(db_path: Path) -> list[str]:
    """Read current alembic version table values from SQLite file."""
    with sqlite3.connect(db_path.as_posix()) as connection:
        cursor = connection.execute("SELECT version_num FROM alembic_version")
        rows = cast("list[tuple[object]]", cursor.fetchall())

    versions: list[str] = []
    for row in rows:
        if len(row) != 1:
            raise AssertionError
        version = row[0]
        if not isinstance(version, str):
            raise TypeError
        versions.append(version)
    return versions


def _db_revision_is_head(db_path: Path) -> bool:
    """Return whether Alembic reports DB revision as head."""
    result = _run_alembic_command(db_path, ("current",))
    if result.returncode != 0:
        raise AssertionError
    output = f"{result.stdout}\n{result.stderr}"
    return "(head)" in output


def _run_alembic_command(
    db_path: Path,
    command_parts: tuple[str, ...],
) -> subprocess.CompletedProcess[str]:
    """Execute Alembic CLI command with explicit DB path for current test."""
    if not ALEMBIC_EXECUTABLE.exists():
        raise AssertionError

    env = os.environ.copy()
    env["TCA_DB_PATH"] = db_path.as_posix()
    return subprocess.run(  # noqa: S603
        [
            ALEMBIC_EXECUTABLE.as_posix(),
            "-c",
            (PROJECT_ROOT / "alembic.ini").as_posix(),
            *command_parts,
        ],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


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
