"""Tests for graceful shutdown sequencing and writer drain behavior."""

from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from tca.api.app import StartupDependencies, create_app, lifespan
from tca.storage import WriterQueue

if TYPE_CHECKING:
    from pathlib import Path

MAX_GRACEFUL_SHUTDOWN_ELAPSED_SECONDS = 0.5


@dataclass(slots=True)
class OrderedDependency:
    """Lifecycle dependency that records shutdown ordering."""

    name: str
    shutdown_events: list[str]

    async def startup(self) -> None:
        """No-op startup hook used by lifecycle tests."""

    async def shutdown(self) -> None:
        """Record deterministic shutdown ordering event."""
        self.shutdown_events.append(f"{self.name}.shutdown")


@dataclass(slots=True)
class HangingSchedulerDependency:
    """Scheduler dependency that exceeds graceful drain timeout."""

    shutdown_delay_seconds: float
    cancelled: bool = False

    async def startup(self) -> None:
        """No-op startup hook used by lifecycle tests."""

    async def shutdown(self) -> None:
        """Sleep for configured delay and track cancellation."""
        try:
            await asyncio.sleep(self.shutdown_delay_seconds)
        except asyncio.CancelledError:
            self.cancelled = True
            raise


@dataclass(slots=True)
class OrderedWriterQueue:
    """Queue-like object that records close ordering for shutdown tests."""

    shutdown_events: list[str]
    closed: bool = False

    async def submit(self, operation: object) -> object:
        """Pass through operation execution for protocol compatibility."""
        return await operation()

    async def close(self) -> None:
        """Record close event used for teardown ordering assertions."""
        self.shutdown_events.append("writer_queue.close")
        self.closed = True


@pytest.fixture(autouse=True)
def _configure_test_db_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Isolate each test with a dedicated SQLite path."""
    db_path = tmp_path / "graceful-shutdown.sqlite3"
    monkeypatch.setenv("TCA_DB_PATH", db_path.as_posix())


@pytest.mark.asyncio
async def test_graceful_shutdown_executes_required_sequence_order() -> None:
    """Shutdown should stop scheduler, flush writer, then close remaining deps."""
    shutdown_events: list[str] = []
    app = create_app()
    app.state.writer_queue_factory = lambda: OrderedWriterQueue(
        shutdown_events=shutdown_events,
    )
    app.state.dependencies = StartupDependencies(
        db=OrderedDependency("db", shutdown_events),
        settings=OrderedDependency("settings", shutdown_events),
        auth=OrderedDependency("auth", shutdown_events),
        telethon_manager=OrderedDependency("telethon_manager", shutdown_events),
        scheduler=OrderedDependency("scheduler", shutdown_events),
    )

    async with lifespan(app):
        pass

    if shutdown_events != [
        "scheduler.shutdown",
        "writer_queue.close",
        "telethon_manager.shutdown",
        "auth.shutdown",
        "settings.shutdown",
        "db.shutdown",
    ]:
        raise AssertionError


@pytest.mark.asyncio
async def test_graceful_shutdown_drains_writer_queue_with_commit_and_rollback(
    tmp_path: Path,
) -> None:
    """In-flight writes complete with commit success and rollback on failure."""
    app = create_app()
    app.state.writer_queue_factory = WriterQueue
    app.state.dependencies = StartupDependencies(
        db=OrderedDependency("db", []),
        settings=OrderedDependency("settings", []),
        auth=OrderedDependency("auth", []),
        telethon_manager=OrderedDependency("telethon_manager", []),
        scheduler=OrderedDependency("scheduler", []),
    )
    db_path = tmp_path / "graceful-shutdown.sqlite3"

    async with lifespan(app):
        runtime = app.state.storage_runtime
        queue = app.state.writer_queue

        async with runtime.write_engine.begin() as connection:
            _ = await connection.exec_driver_sql(
                """
                CREATE TABLE graceful_shutdown_writes (
                    id INTEGER PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """,
            )

        async def _commit_write() -> str:
            async with runtime.write_session_factory() as session:
                _ = await session.execute(
                    text(
                        """
                        INSERT INTO graceful_shutdown_writes (id, value)
                        VALUES (:id, :value)
                        """,
                    ),
                    {"id": 1, "value": "committed"},
                )
                await session.commit()
            return "committed"

        async def _rollback_write() -> str:
            async with runtime.write_session_factory() as session:
                _ = await session.execute(
                    text(
                        """
                        INSERT INTO graceful_shutdown_writes (id, value)
                        VALUES (:id, :value)
                        """,
                    ),
                    {"id": 2, "value": "rolled-back"},
                )
                message = "forced-write-failure"
                raise RuntimeError(message)

        commit_task = asyncio.create_task(queue.submit(_commit_write))
        rollback_task = asyncio.create_task(queue.submit(_rollback_write))
        await asyncio.sleep(0)

    commit_result = await commit_task
    rollback_result = await asyncio.gather(rollback_task, return_exceptions=True)

    if commit_result != "committed":
        raise AssertionError
    rollback_error = rollback_result[0]
    if not isinstance(rollback_error, RuntimeError):
        message = (
            "Expected RuntimeError from rollback write, "
            f"got {type(rollback_error).__name__}."
        )
        raise TypeError(message)
    if str(rollback_error) != "forced-write-failure":
        raise AssertionError

    with sqlite3.connect(db_path.as_posix()) as connection:
        committed_count = connection.execute(
            "SELECT COUNT(*) FROM graceful_shutdown_writes WHERE id = 1",
        ).fetchone()
        rolled_back_count = connection.execute(
            "SELECT COUNT(*) FROM graceful_shutdown_writes WHERE id = 2",
        ).fetchone()

    if committed_count is None or committed_count[0] != 1:
        raise AssertionError
    if rolled_back_count is None or rolled_back_count[0] != 0:
        raise AssertionError


@pytest.mark.asyncio
async def test_graceful_shutdown_exits_before_timeout_when_scheduler_hangs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Teardown should complete promptly when scheduler drain exceeds timeout."""
    timeout_seconds = 0.05
    shutdown_events: list[str] = []
    scheduler = HangingSchedulerDependency(shutdown_delay_seconds=1.0)
    app = create_app()
    app.state.writer_queue_factory = lambda: OrderedWriterQueue(
        shutdown_events=shutdown_events,
    )
    app.state.dependencies = StartupDependencies(
        db=OrderedDependency("db", shutdown_events),
        settings=OrderedDependency("settings", shutdown_events),
        auth=OrderedDependency("auth", shutdown_events),
        telethon_manager=OrderedDependency("telethon_manager", shutdown_events),
        scheduler=scheduler,
    )
    monkeypatch.setattr(
        "tca.api.app.SCHEDULER_SHUTDOWN_TIMEOUT_SECONDS",
        timeout_seconds,
    )

    started = time.monotonic()
    async with lifespan(app):
        pass
    elapsed_seconds = time.monotonic() - started

    if elapsed_seconds >= MAX_GRACEFUL_SHUTDOWN_ELAPSED_SECONDS:
        raise AssertionError
    if not scheduler.cancelled:
        raise AssertionError
    if shutdown_events != [
        "writer_queue.close",
        "telethon_manager.shutdown",
        "auth.shutdown",
        "settings.shutdown",
        "db.shutdown",
    ]:
        raise AssertionError
