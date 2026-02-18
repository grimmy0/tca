"""Nightly SQLite backup job using SQLite Online Backup API."""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from tca.storage import NotificationsRepository

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import date
    from pathlib import Path

    from tca.storage.db import SessionFactory

BACKUP_FAILURE_NOTIFICATION_TYPE = "backup_failure"
BACKUP_FAILURE_NOTIFICATION_SEVERITY = "high"
BACKUP_FAILURE_NOTIFICATION_MESSAGE = "Nightly SQLite backup failed."


@dataclass(slots=True, frozen=True)
class BackupJobRunSummary:
    """Result details for one nightly backup execution."""

    backup_path: Path
    integrity_check_result: str


class NightlySQLiteBackupError(RuntimeError):
    """Raised when the nightly SQLite backup job fails."""


class NightlySQLiteBackupJob:
    """Run one nightly SQLite backup and verify integrity."""

    _read_session_factory: SessionFactory
    _write_session_factory: SessionFactory
    _db_path: Path
    _backup_dir: Path
    _now_provider: Callable[[], datetime]

    def __init__(
        self,
        *,
        read_session_factory: SessionFactory,
        write_session_factory: SessionFactory,
        db_path: Path,
        backup_dir: Path | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        """Create backup job with storage dependencies and filesystem paths."""
        self._read_session_factory = read_session_factory
        self._write_session_factory = write_session_factory
        self._db_path = db_path
        self._backup_dir = backup_dir or (db_path.parent / "backups")
        self._now_provider = now_provider or _utc_now

    async def run_once(self) -> BackupJobRunSummary:
        """Create nightly backup file and validate it with integrity check."""
        run_at = _normalize_datetime(self._now_provider())
        backup_path = self._backup_path_for_date(run_date=run_at.date())
        try:
            integrity_check_result = await asyncio.to_thread(
                _create_backup_and_run_integrity_check,
                source_db_path=self._db_path,
                backup_path=backup_path,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._create_failure_notification(
                backup_path=backup_path,
                run_at=run_at,
                error=exc,
            )
            msg = "Nightly SQLite backup job failed."
            raise NightlySQLiteBackupError(msg) from exc
        return BackupJobRunSummary(
            backup_path=backup_path,
            integrity_check_result=integrity_check_result,
        )

    async def _create_failure_notification(
        self,
        *,
        backup_path: Path,
        run_at: datetime,
        error: Exception,
    ) -> None:
        repository = NotificationsRepository(
            read_session_factory=self._read_session_factory,
            write_session_factory=self._write_session_factory,
        )
        _ = await repository.create(
            notification_type=BACKUP_FAILURE_NOTIFICATION_TYPE,
            severity=BACKUP_FAILURE_NOTIFICATION_SEVERITY,
            message=BACKUP_FAILURE_NOTIFICATION_MESSAGE,
            payload={
                "backup_path": backup_path.as_posix(),
                "error_type": error.__class__.__name__,
                "error_message": str(error),
                "failed_at": run_at.isoformat(),
            },
        )

    def _backup_path_for_date(self, *, run_date: date) -> Path:
        file_name = f"tca-{run_date.strftime('%Y%m%d')}.db"
        return self._backup_dir / file_name


def _create_backup_and_run_integrity_check(
    *,
    source_db_path: Path,
    backup_path: Path,
) -> str:
    if not source_db_path.exists():
        msg = f"Source SQLite database does not exist: {source_db_path.as_posix()}"
        raise FileNotFoundError(msg)

    backup_dir = backup_path.parent
    backup_dir.mkdir(parents=True, exist_ok=True)
    temporary_backup_path = backup_path.with_suffix(f"{backup_path.suffix}.tmp")

    if temporary_backup_path.exists():
        temporary_backup_path.unlink()

    try:
        with (
            sqlite3.connect(source_db_path.as_posix()) as source_connection,
            sqlite3.connect(temporary_backup_path.as_posix()) as destination_connection,
        ):
            source_connection.backup(destination_connection)

        integrity_check_result = _run_integrity_check(
            backup_path=temporary_backup_path,
        )
        if integrity_check_result != "ok":
            _raise_integrity_check_failure(result=integrity_check_result)
        _ = temporary_backup_path.replace(backup_path)
    except Exception:
        if temporary_backup_path.exists():
            temporary_backup_path.unlink()
        raise
    return "ok"


def _run_integrity_check(*, backup_path: Path) -> str:
    with sqlite3.connect(backup_path.as_posix()) as backup_connection:
        row = cast(
            "object",
            backup_connection.execute("PRAGMA integrity_check").fetchone(),
        )
    return _coerce_integrity_check_row(row=row)


def _coerce_integrity_check_row(*, row: object) -> str:
    if not isinstance(row, tuple):
        msg = "PRAGMA integrity_check returned invalid row shape."
        raise NightlySQLiteBackupError(msg)
    row_tuple = cast("tuple[object, ...]", row)
    if len(row_tuple) != 1:
        msg = "PRAGMA integrity_check returned unexpected column count."
        raise NightlySQLiteBackupError(msg)
    value = row_tuple[0]
    if not isinstance(value, str):
        msg = "PRAGMA integrity_check returned non-string result."
        raise NightlySQLiteBackupError(msg)
    return value.lower()


def _raise_integrity_check_failure(*, result: str) -> None:
    msg = f"SQLite backup integrity_check failed with result {result!r}."
    raise NightlySQLiteBackupError(msg)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
