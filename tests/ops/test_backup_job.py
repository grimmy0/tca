"""Tests for nightly SQLite backup job behavior."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from tca.config.settings import load_settings
from tca.ops.backup_job import (
    BACKUP_FAILURE_NOTIFICATION_MESSAGE,
    BACKUP_FAILURE_NOTIFICATION_SEVERITY,
    BACKUP_FAILURE_NOTIFICATION_TYPE,
    NightlySQLiteBackupError,
    NightlySQLiteBackupJob,
)
from tca.storage import (
    NotificationsRepository,
    StorageRuntime,
    create_storage_runtime,
    dispose_storage_runtime,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


@pytest.fixture
async def notifications_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[StorageRuntime]:
    """Build a SQLite runtime containing notifications table."""
    db_path = tmp_path / "backup-job-notifications.sqlite3"
    monkeypatch.setenv("TCA_DB_PATH", db_path.as_posix())
    settings = load_settings()
    runtime = create_storage_runtime(settings)

    async with runtime.write_engine.begin() as connection:
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY,
                type VARCHAR(64) NOT NULL,
                severity VARCHAR(16) NOT NULL,
                message TEXT NOT NULL,
                payload_json TEXT NULL,
                is_acknowledged BOOLEAN NOT NULL DEFAULT 0,
                acknowledged_at DATETIME NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
        )

    try:
        yield runtime
    finally:
        await dispose_storage_runtime(runtime)


@pytest.mark.asyncio
async def test_backup_job_creates_backup_file_with_expected_naming_format(
    tmp_path: Path,
    notifications_runtime: StorageRuntime,
) -> None:
    """Nightly backup should create `tca-YYYYMMDD.db` file in backup directory."""
    source_db_path = tmp_path / "source.sqlite3"
    _create_source_database(db_path=source_db_path)
    fixed_now = datetime.now(UTC).replace(hour=1, minute=30, second=0, microsecond=0)
    expected_file_name = f"tca-{fixed_now.strftime('%Y%m%d')}.db"
    backup_dir = tmp_path / "backups"
    job = NightlySQLiteBackupJob(
        read_session_factory=notifications_runtime.read_session_factory,
        write_session_factory=notifications_runtime.write_session_factory,
        db_path=source_db_path,
        backup_dir=backup_dir,
        now_provider=lambda: fixed_now,
    )

    summary = await job.run_once()

    if summary.backup_path.name != expected_file_name:
        raise AssertionError
    if summary.backup_path != backup_dir / expected_file_name:
        raise AssertionError
    if not summary.backup_path.exists():
        raise AssertionError


@pytest.mark.asyncio
async def test_backup_job_runs_integrity_check_for_created_backup(
    tmp_path: Path,
    notifications_runtime: StorageRuntime,
) -> None:
    """Nightly backup should pass SQLite PRAGMA integrity_check on backup file."""
    source_db_path = tmp_path / "source-integrity.sqlite3"
    _create_source_database(db_path=source_db_path)
    backup_dir = tmp_path / "backups-integrity"
    fixed_now = datetime.now(UTC).replace(hour=2, minute=30, second=0, microsecond=0)
    job = NightlySQLiteBackupJob(
        read_session_factory=notifications_runtime.read_session_factory,
        write_session_factory=notifications_runtime.write_session_factory,
        db_path=source_db_path,
        backup_dir=backup_dir,
        now_provider=lambda: fixed_now,
    )

    summary = await job.run_once()
    integrity_result = _run_integrity_check(db_path=summary.backup_path)

    if summary.integrity_check_result != "ok":
        raise AssertionError
    if integrity_result != "ok":
        raise AssertionError


@pytest.mark.asyncio
async def test_backup_job_is_idempotent_for_same_run_date(
    tmp_path: Path,
    notifications_runtime: StorageRuntime,
) -> None:
    """Running backup twice for same date should overwrite output safely."""
    source_db_path = tmp_path / "source-idempotent.sqlite3"
    _create_source_database(db_path=source_db_path)
    backup_dir = tmp_path / "backups-idempotent"
    fixed_now = datetime.now(UTC).replace(hour=2, minute=45, second=0, microsecond=0)
    job = NightlySQLiteBackupJob(
        read_session_factory=notifications_runtime.read_session_factory,
        write_session_factory=notifications_runtime.write_session_factory,
        db_path=source_db_path,
        backup_dir=backup_dir,
        now_provider=lambda: fixed_now,
    )

    first_summary = await job.run_once()
    second_summary = await job.run_once()
    second_integrity_result = _run_integrity_check(db_path=second_summary.backup_path)

    if first_summary.backup_path != second_summary.backup_path:
        raise AssertionError
    if second_summary.integrity_check_result != "ok":
        raise AssertionError
    if second_integrity_result != "ok":
        raise AssertionError


@pytest.mark.asyncio
async def test_backup_job_failure_creates_notification(
    tmp_path: Path,
    notifications_runtime: StorageRuntime,
) -> None:
    """Backup failure should persist a high-severity backup notification."""
    missing_source_db_path = tmp_path / "missing-source.sqlite3"
    backup_dir = tmp_path / "failed-backups"
    fixed_now = datetime.now(UTC).replace(hour=3, minute=30, second=0, microsecond=0)
    expected_file_name = f"tca-{fixed_now.strftime('%Y%m%d')}.db"
    job = NightlySQLiteBackupJob(
        read_session_factory=notifications_runtime.read_session_factory,
        write_session_factory=notifications_runtime.write_session_factory,
        db_path=missing_source_db_path,
        backup_dir=backup_dir,
        now_provider=lambda: fixed_now,
    )

    with pytest.raises(NightlySQLiteBackupError):
        _ = await job.run_once()

    repository = NotificationsRepository(
        read_session_factory=notifications_runtime.read_session_factory,
        write_session_factory=notifications_runtime.write_session_factory,
    )
    notifications = await repository.list_notifications()
    if len(notifications) != 1:
        raise AssertionError
    notification = notifications[0]
    if notification.type != BACKUP_FAILURE_NOTIFICATION_TYPE:
        raise AssertionError
    if notification.severity != BACKUP_FAILURE_NOTIFICATION_SEVERITY:
        raise AssertionError
    if notification.message != BACKUP_FAILURE_NOTIFICATION_MESSAGE:
        raise AssertionError
    if notification.payload is None:
        raise AssertionError
    if not isinstance(notification.payload, dict):
        raise TypeError
    if (
        notification.payload.get("backup_path")
        != (backup_dir / expected_file_name).as_posix()
    ):
        raise AssertionError
    if notification.payload.get("error_type") != "FileNotFoundError":
        raise AssertionError


@pytest.mark.asyncio
async def test_backup_job_cancellation_propagates_without_notification(
    tmp_path: Path,
    notifications_runtime: StorageRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancelled backup execution should not be remapped to backup failure."""
    source_db_path = tmp_path / "source-cancelled.sqlite3"
    _create_source_database(db_path=source_db_path)
    fixed_now = datetime.now(UTC).replace(hour=4, minute=30, second=0, microsecond=0)
    backup_dir = tmp_path / "cancelled-backups"
    job = NightlySQLiteBackupJob(
        read_session_factory=notifications_runtime.read_session_factory,
        write_session_factory=notifications_runtime.write_session_factory,
        db_path=source_db_path,
        backup_dir=backup_dir,
        now_provider=lambda: fixed_now,
    )

    async def _raise_cancelled(*_args: object, **_kwargs: object) -> str:
        raise asyncio.CancelledError

    monkeypatch.setattr("tca.ops.backup_job.asyncio.to_thread", _raise_cancelled)

    with pytest.raises(asyncio.CancelledError):
        _ = await job.run_once()

    repository = NotificationsRepository(
        read_session_factory=notifications_runtime.read_session_factory,
        write_session_factory=notifications_runtime.write_session_factory,
    )
    notifications = await repository.list_notifications()
    if notifications:
        raise AssertionError


@pytest.mark.asyncio
async def test_backup_job_cleanup_failure_creates_notification(
    tmp_path: Path,
    notifications_runtime: StorageRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cleanup failures should be remapped and recorded as backup failures."""
    source_db_path = tmp_path / "source-cleanup-failure.sqlite3"
    _create_source_database(db_path=source_db_path)
    fixed_now = datetime.now(UTC).replace(hour=5, minute=30, second=0, microsecond=0)
    backup_dir = tmp_path / "cleanup-failure-backups"
    expected_file_name = f"tca-{fixed_now.strftime('%Y%m%d')}.db"
    job = NightlySQLiteBackupJob(
        read_session_factory=notifications_runtime.read_session_factory,
        write_session_factory=notifications_runtime.write_session_factory,
        db_path=source_db_path,
        backup_dir=backup_dir,
        now_provider=lambda: fixed_now,
    )

    def _raise_cleanup_failure(*, backup_dir: Path, retain_count: int) -> None:
        _ = (backup_dir, retain_count)
        msg = "forced cleanup failure"
        raise PermissionError(msg)

    monkeypatch.setattr(
        "tca.ops.backup_job._cleanup_old_backups",
        _raise_cleanup_failure,
    )

    with pytest.raises(NightlySQLiteBackupError):
        _ = await job.run_once()

    repository = NotificationsRepository(
        read_session_factory=notifications_runtime.read_session_factory,
        write_session_factory=notifications_runtime.write_session_factory,
    )
    notifications = await repository.list_notifications()
    if len(notifications) != 1:
        raise AssertionError
    notification = notifications[0]
    if notification.type != BACKUP_FAILURE_NOTIFICATION_TYPE:
        raise AssertionError
    if notification.severity != BACKUP_FAILURE_NOTIFICATION_SEVERITY:
        raise AssertionError
    if notification.message != BACKUP_FAILURE_NOTIFICATION_MESSAGE:
        raise AssertionError
    if notification.payload is None:
        raise AssertionError
    if not isinstance(notification.payload, dict):
        raise TypeError
    if (
        notification.payload.get("backup_path")
        != (backup_dir / expected_file_name).as_posix()
    ):
        raise AssertionError
    if notification.payload.get("error_type") != "PermissionError":
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
            (1, "backup-row"),
        )


def _run_integrity_check(*, db_path: Path) -> str:
    with sqlite3.connect(db_path.as_posix()) as connection:
        row = connection.execute("PRAGMA integrity_check").fetchone()
    if not isinstance(row, tuple):
        raise TypeError
    if len(row) != 1:
        raise AssertionError
    value = row[0]
    if not isinstance(value, str):
        raise TypeError
    return value.lower()
