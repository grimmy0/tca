"""Tests for nightly backup retention cleanup behavior."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.exc import SQLAlchemyError

from tca.config.settings import load_settings
from tca.ops.backup_job import NightlySQLiteBackupJob
from tca.storage import (
    SettingsRepository,
    StorageRuntime,
    create_storage_runtime,
    dispose_storage_runtime,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


@pytest.fixture
async def runtime_with_settings(tmp_path: Path) -> AsyncIterator[StorageRuntime]:
    """Create storage runtime containing `settings` table for dynamic reads."""
    db_path = tmp_path / "backup-retention-runtime.sqlite3"
    settings = load_settings({"TCA_DB_PATH": db_path.as_posix()})
    runtime = create_storage_runtime(settings)
    async with runtime.write_engine.begin() as connection:
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY,
                key VARCHAR(255) NOT NULL,
                value_json TEXT NOT NULL,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_settings_key UNIQUE (key)
            )
            """,
        )
    try:
        yield runtime
    finally:
        await dispose_storage_runtime(runtime)


@pytest.mark.asyncio
async def test_backup_retention_removes_older_backups_beyond_retain_count(
    tmp_path: Path,
    runtime_with_settings: StorageRuntime,
) -> None:
    """Backups older than retain count should be deleted after a run."""
    source_db_path = tmp_path / "retention-source.sqlite3"
    _create_source_database(db_path=source_db_path)
    backup_dir = tmp_path / "retention-backups"
    run_at = datetime.now(UTC).replace(hour=1, minute=0, second=0, microsecond=0)
    run_date = run_at.date()
    _create_placeholder_backups(
        backup_dir=backup_dir,
        names=(
            _backup_name_for_date(run_date - timedelta(days=4)),
            _backup_name_for_date(run_date - timedelta(days=3)),
            _backup_name_for_date(run_date - timedelta(days=2)),
            _backup_name_for_date(run_date - timedelta(days=1)),
        ),
    )
    repository = SettingsRepository(
        read_session_factory=runtime_with_settings.read_session_factory,
        write_session_factory=runtime_with_settings.write_session_factory,
    )
    _ = await repository.create(key="backup.retain_count", value=2)
    job = NightlySQLiteBackupJob(
        read_session_factory=runtime_with_settings.read_session_factory,
        write_session_factory=runtime_with_settings.write_session_factory,
        db_path=source_db_path,
        backup_dir=backup_dir,
        now_provider=lambda: run_at,
    )

    _ = await job.run_once()

    names = _list_backup_names(backup_dir=backup_dir)
    if names != [
        _backup_name_for_date(run_date - timedelta(days=1)),
        _backup_name_for_date(run_date),
    ]:
        raise AssertionError


@pytest.mark.asyncio
async def test_backup_retention_keeps_newest_n_files(
    tmp_path: Path,
    runtime_with_settings: StorageRuntime,
) -> None:
    """Cleanup should preserve the newest N backups by file date naming."""
    source_db_path = tmp_path / "retention-source-newest.sqlite3"
    _create_source_database(db_path=source_db_path)
    backup_dir = tmp_path / "retention-backups-newest"
    run_at = datetime.now(UTC).replace(hour=2, minute=0, second=0, microsecond=0)
    run_date = run_at.date()
    _create_placeholder_backups(
        backup_dir=backup_dir,
        names=(
            _backup_name_for_date(run_date - timedelta(days=4)),
            _backup_name_for_date(run_date - timedelta(days=3)),
            _backup_name_for_date(run_date - timedelta(days=2)),
            _backup_name_for_date(run_date - timedelta(days=1)),
        ),
    )
    repository = SettingsRepository(
        read_session_factory=runtime_with_settings.read_session_factory,
        write_session_factory=runtime_with_settings.write_session_factory,
    )
    _ = await repository.create(key="backup.retain_count", value=3)
    job = NightlySQLiteBackupJob(
        read_session_factory=runtime_with_settings.read_session_factory,
        write_session_factory=runtime_with_settings.write_session_factory,
        db_path=source_db_path,
        backup_dir=backup_dir,
        now_provider=lambda: run_at,
    )

    _ = await job.run_once()

    names = _list_backup_names(backup_dir=backup_dir)
    if names != [
        _backup_name_for_date(run_date - timedelta(days=2)),
        _backup_name_for_date(run_date - timedelta(days=1)),
        _backup_name_for_date(run_date),
    ]:
        raise AssertionError


@pytest.mark.asyncio
async def test_backup_retention_uses_updated_setting_without_restart(
    tmp_path: Path,
    runtime_with_settings: StorageRuntime,
) -> None:
    """Job should resolve retain count on each run from settings table."""
    source_db_path = tmp_path / "retention-source-dynamic.sqlite3"
    _create_source_database(db_path=source_db_path)
    backup_dir = tmp_path / "retention-backups-dynamic"
    first_run_at = datetime.now(UTC).replace(hour=3, minute=0, second=0, microsecond=0)
    first_run_date = first_run_at.date()
    _create_placeholder_backups(
        backup_dir=backup_dir,
        names=(
            _backup_name_for_date(first_run_date - timedelta(days=4)),
            _backup_name_for_date(first_run_date - timedelta(days=3)),
            _backup_name_for_date(first_run_date - timedelta(days=2)),
            _backup_name_for_date(first_run_date - timedelta(days=1)),
        ),
    )
    repository = SettingsRepository(
        read_session_factory=runtime_with_settings.read_session_factory,
        write_session_factory=runtime_with_settings.write_session_factory,
    )
    _ = await repository.create(key="backup.retain_count", value=3)
    current_time = {"value": first_run_at}
    job = NightlySQLiteBackupJob(
        read_session_factory=runtime_with_settings.read_session_factory,
        write_session_factory=runtime_with_settings.write_session_factory,
        db_path=source_db_path,
        backup_dir=backup_dir,
        now_provider=lambda: current_time["value"],
    )

    _ = await job.run_once()
    names_after_first_run = _list_backup_names(backup_dir=backup_dir)
    if names_after_first_run != [
        _backup_name_for_date(first_run_date - timedelta(days=2)),
        _backup_name_for_date(first_run_date - timedelta(days=1)),
        _backup_name_for_date(first_run_date),
    ]:
        raise AssertionError

    _ = await repository.update(key="backup.retain_count", value=1)
    second_run_date = first_run_date + timedelta(days=1)
    current_time["value"] = first_run_at + timedelta(days=1)
    _ = await job.run_once()

    names_after_second_run = _list_backup_names(backup_dir=backup_dir)
    if names_after_second_run != [_backup_name_for_date(second_run_date)]:
        raise AssertionError


@pytest.mark.asyncio
async def test_backup_retention_falls_back_to_default_when_setting_lookup_fails(
    tmp_path: Path,
    runtime_with_settings: StorageRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retention cleanup should use default count if settings read fails."""
    source_db_path = tmp_path / "retention-source-default-fallback.sqlite3"
    _create_source_database(db_path=source_db_path)
    backup_dir = tmp_path / "retention-backups-default-fallback"
    run_at = datetime.now(UTC).replace(hour=5, minute=0, second=0, microsecond=0)
    run_date = run_at.date()
    _create_placeholder_backups(
        backup_dir=backup_dir,
        names=tuple(
            _backup_name_for_date(run_date - timedelta(days=days_ago))
            for days_ago in range(20, 0, -1)
        ),
    )
    _ = await SettingsRepository(
        read_session_factory=runtime_with_settings.read_session_factory,
        write_session_factory=runtime_with_settings.write_session_factory,
    ).create(key="backup.retain_count", value=1)
    job = NightlySQLiteBackupJob(
        read_session_factory=runtime_with_settings.read_session_factory,
        write_session_factory=runtime_with_settings.write_session_factory,
        db_path=source_db_path,
        backup_dir=backup_dir,
        now_provider=lambda: run_at,
    )

    async def _raise_sqlalchemy_error(
        self: SettingsRepository,
        *,
        key: str,
    ) -> None:
        _ = (self, key)
        msg = "forced settings lookup failure"
        raise SQLAlchemyError(msg)

    monkeypatch.setattr(SettingsRepository, "get_by_key", _raise_sqlalchemy_error)

    _ = await job.run_once()

    names = _list_backup_names(backup_dir=backup_dir)
    expected_names = [
        _backup_name_for_date(run_date - timedelta(days=days_ago))
        for days_ago in range(13, -1, -1)
    ]
    if names != expected_names:
        raise AssertionError


def _create_source_database(*, db_path: Path) -> None:
    with sqlite3.connect(db_path.as_posix()) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sample_data (
                id INTEGER PRIMARY KEY,
                value TEXT NOT NULL
            )
            """,
        )
        connection.execute(
            "INSERT INTO sample_data (id, value) VALUES (?, ?)",
            (1, "retention-row"),
        )


def _create_placeholder_backups(*, backup_dir: Path, names: tuple[str, ...]) -> None:
    backup_dir.mkdir(parents=True, exist_ok=True)
    for name in names:
        path = backup_dir / name
        path.write_text("placeholder", encoding="utf-8")


def _list_backup_names(*, backup_dir: Path) -> list[str]:
    return sorted(path.name for path in backup_dir.glob("tca-*.db") if path.is_file())


def _backup_name_for_date(run_date: date) -> str:
    return f"tca-{run_date.strftime('%Y%m%d')}.db"
