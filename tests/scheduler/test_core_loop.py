"""Tests for scheduler core loop selection and lifecycle behavior."""

from __future__ import annotations

import itertools
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from collections.abc import Callable

import pytest
from sqlalchemy import text

from tca.config.settings import load_settings
from tca.scheduler import SchedulerCoreLoop, SchedulerService
from tca.storage import (
    ChannelStateRepository,
    ChannelsRepository,
    PollJobsRepository,
    StorageRuntime,
    create_storage_runtime,
    dispose_storage_runtime,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


@pytest.fixture
async def scheduler_runtime(tmp_path: Path) -> AsyncIterator[StorageRuntime]:
    """Create scheduler runtime fixture with required tables."""
    db_path = tmp_path / "scheduler-core-loop.sqlite3"
    settings = load_settings({"TCA_DB_PATH": db_path.as_posix()})
    runtime = create_storage_runtime(settings)

    async with runtime.write_engine.begin() as connection:
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS telegram_accounts (
                id INTEGER PRIMARY KEY,
                api_id INTEGER NOT NULL,
                api_hash_encrypted BLOB NOT NULL,
                paused_at DATETIME NULL
            )
            """,
        )
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS telegram_channels (
                id INTEGER PRIMARY KEY,
                account_id INTEGER NOT NULL,
                telegram_channel_id BIGINT NOT NULL,
                name VARCHAR(255) NOT NULL,
                username VARCHAR(255) NULL,
                is_enabled BOOLEAN NOT NULL DEFAULT 1,
                CONSTRAINT fk_telegram_channels_account_id
                    FOREIGN KEY (account_id)
                    REFERENCES telegram_accounts(id)
                    ON DELETE CASCADE,
                CONSTRAINT uq_telegram_channels_telegram_channel_id
                    UNIQUE (telegram_channel_id)
            )
            """,
        )
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS channel_state (
                channel_id INTEGER PRIMARY KEY,
                cursor_json TEXT NULL,
                paused_until DATETIME NULL,
                last_success_at DATETIME NULL,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_channel_state_channel_id
                    FOREIGN KEY (channel_id)
                    REFERENCES telegram_channels(id)
                    ON DELETE CASCADE
            )
            """,
        )
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS poll_jobs (
                id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                correlation_id VARCHAR(64) NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_poll_jobs_channel_id
                    FOREIGN KEY (channel_id)
                    REFERENCES telegram_channels(id)
                    ON DELETE CASCADE
            )
            """,
        )
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
async def test_next_run_at_selection_uses_last_success_at(
    scheduler_runtime: StorageRuntime,
) -> None:
    """Ensure scheduler selects eligible channels by next_run_at logic."""
    await _seed_account(scheduler_runtime, account_id=1)
    await _seed_channel(
        scheduler_runtime,
        channel_id=1,
        account_id=1,
        telegram_channel_id=101,
        name="alpha",
        is_enabled=True,
    )
    await _seed_channel(
        scheduler_runtime,
        channel_id=2,
        account_id=1,
        telegram_channel_id=202,
        name="beta",
        is_enabled=True,
    )
    await _seed_channel(
        scheduler_runtime,
        channel_id=3,
        account_id=1,
        telegram_channel_id=303,
        name="gamma",
        is_enabled=True,
    )

    now = datetime(2026, 2, 18, 12, 0, 0, tzinfo=timezone.utc)
    await _seed_state(
        scheduler_runtime,
        channel_id=1,
        last_success_at=now - timedelta(seconds=301),
    )
    await _seed_state(
        scheduler_runtime,
        channel_id=2,
        last_success_at=now - timedelta(seconds=100),
    )

    core_loop = _build_core_loop(scheduler_runtime, now=now)
    await core_loop.run_once()

    if await _read_poll_job_channel_ids(scheduler_runtime) != [1, 3]:
        raise AssertionError


@pytest.mark.asyncio
async def test_naive_last_success_at_is_normalized(
    scheduler_runtime: StorageRuntime,
) -> None:
    """Ensure naive last_success_at values are treated as UTC."""
    await _seed_account(scheduler_runtime, account_id=1)
    await _seed_channel(
        scheduler_runtime,
        channel_id=1,
        account_id=1,
        telegram_channel_id=404,
        name="delta",
        is_enabled=True,
    )

    now = datetime(2026, 2, 18, 13, 0, 0, tzinfo=timezone.utc)
    naive_last_success = datetime(2026, 2, 18, 12, 55, 0)
    await _seed_state(
        scheduler_runtime,
        channel_id=1,
        last_success_at=naive_last_success,
    )

    core_loop = _build_core_loop(scheduler_runtime, now=now)
    await core_loop.run_once()

    if await _read_poll_job_channel_ids(scheduler_runtime) != [1]:
        raise AssertionError


@pytest.mark.asyncio
async def test_disabled_channels_are_excluded_from_scheduler(
    scheduler_runtime: StorageRuntime,
) -> None:
    """Ensure disabled channels do not receive poll jobs."""
    await _seed_account(scheduler_runtime, account_id=1)
    await _seed_channel(
        scheduler_runtime,
        channel_id=10,
        account_id=1,
        telegram_channel_id=1001,
        name="enabled",
        is_enabled=True,
    )
    await _seed_channel(
        scheduler_runtime,
        channel_id=11,
        account_id=1,
        telegram_channel_id=1002,
        name="disabled",
        is_enabled=False,
    )

    now = datetime(2026, 2, 18, 12, 15, 0, tzinfo=timezone.utc)
    await _seed_state(
        scheduler_runtime,
        channel_id=10,
        last_success_at=now - timedelta(seconds=400),
    )
    await _seed_state(
        scheduler_runtime,
        channel_id=11,
        last_success_at=now - timedelta(seconds=400),
    )

    core_loop = _build_core_loop(scheduler_runtime, now=now)
    await core_loop.run_once()

    if await _read_poll_job_channel_ids(scheduler_runtime) != [10]:
        raise AssertionError


@pytest.mark.asyncio
async def test_paused_channels_are_skipped_by_scheduler(
    scheduler_runtime: StorageRuntime,
) -> None:
    """Ensure paused channels are skipped by scheduler selection."""
    await _seed_account(scheduler_runtime, account_id=3)
    await _seed_channel(
        scheduler_runtime,
        channel_id=20,
        account_id=3,
        telegram_channel_id=2001,
        name="paused",
        is_enabled=True,
    )
    await _seed_channel(
        scheduler_runtime,
        channel_id=21,
        account_id=3,
        telegram_channel_id=2002,
        name="active",
        is_enabled=True,
    )

    now = datetime(2026, 2, 18, 12, 45, 0, tzinfo=timezone.utc)
    await _seed_state(
        scheduler_runtime,
        channel_id=20,
        last_success_at=now - timedelta(seconds=400),
        paused_until=now + timedelta(seconds=600),
    )
    await _seed_state(
        scheduler_runtime,
        channel_id=21,
        last_success_at=now - timedelta(seconds=400),
        paused_until=None,
    )

    core_loop = _build_core_loop(scheduler_runtime, now=now)
    await core_loop.run_once()

    if await _read_poll_job_channel_ids(scheduler_runtime) != [21]:
        raise AssertionError


@pytest.mark.asyncio
async def test_scheduler_service_starts_and_stops_cleanly(
    scheduler_runtime: StorageRuntime,
) -> None:
    """Ensure scheduler lifecycle hooks start and stop without errors."""
    now = datetime(2026, 2, 18, 12, 30, 0, tzinfo=timezone.utc)
    service = SchedulerService(
        runtime_provider=lambda: scheduler_runtime,
        poll_interval_seconds=300,
        tick_interval_seconds=0.01,
        time_provider=lambda: now,
        correlation_id_factory=_build_correlation_factory(),
    )

    await service.startup()
    if not service.is_running:
        raise AssertionError
    await service.shutdown()
    if service.is_running:
        raise AssertionError


def _build_core_loop(runtime: StorageRuntime, *, now: datetime) -> SchedulerCoreLoop:
    return SchedulerCoreLoop(
        channels_repository=ChannelsRepository(
            read_session_factory=runtime.read_session_factory,
            write_session_factory=runtime.write_session_factory,
        ),
        state_repository=ChannelStateRepository(
            read_session_factory=runtime.read_session_factory,
            write_session_factory=runtime.write_session_factory,
        ),
        jobs_repository=PollJobsRepository(
            read_session_factory=runtime.read_session_factory,
            write_session_factory=runtime.write_session_factory,
        ),
        poll_interval_seconds=300,
        jitter_ratio=0.0,
        time_provider=lambda: now,
        correlation_id_factory=_build_correlation_factory(),
    )


async def _seed_account(runtime: StorageRuntime, *, account_id: int) -> None:
    async with runtime.write_session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO telegram_accounts (id, api_id, api_hash_encrypted)
                VALUES (:id, :api_id, :api_hash_encrypted)
                """,
            ),
            {
                "id": account_id,
                "api_id": 12345,
                "api_hash_encrypted": b"encrypted-api-hash",
            },
        )
        await session.commit()


async def _seed_channel(
    runtime: StorageRuntime,
    *,
    channel_id: int,
    account_id: int,
    telegram_channel_id: int,
    name: str,
    is_enabled: bool,
) -> None:
    async with runtime.write_session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO telegram_channels (
                    id,
                    account_id,
                    telegram_channel_id,
                    name,
                    is_enabled
                )
                VALUES (:id, :account_id, :telegram_channel_id, :name, :is_enabled)
                """,
            ),
            {
                "id": channel_id,
                "account_id": account_id,
                "telegram_channel_id": telegram_channel_id,
                "name": name,
                "is_enabled": int(is_enabled),
            },
        )
        await session.commit()


async def _seed_state(
    runtime: StorageRuntime,
    *,
    channel_id: int,
    last_success_at: datetime,
    paused_until: datetime | None = None,
) -> None:
    async with runtime.write_session_factory() as session:
        await session.execute(
            text(
                """
                INSERT INTO channel_state (channel_id, last_success_at, paused_until)
                VALUES (:channel_id, :last_success_at, :paused_until)
                """,
            ),
            {
                "channel_id": channel_id,
                "last_success_at": last_success_at,
                "paused_until": paused_until,
            },
        )
        await session.commit()


async def _read_poll_job_channel_ids(runtime: StorageRuntime) -> list[int]:
    async with runtime.read_session_factory() as session:
        result = await session.execute(
            text(
                """
                SELECT channel_id
                FROM poll_jobs
                ORDER BY id ASC
                """,
            ),
        )
        rows = result.scalars().all()
    return list(rows)


def _build_correlation_factory() -> Callable[[], str]:
    counter = itertools.count(1)
    return lambda: f"job-{next(counter)}"
