"""Tests for scheduler jitter and poll interval settings."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from tca.config.settings import load_settings
from tca.scheduler.service import (
    DEFAULT_POLL_INTERVAL_SECONDS,
    SchedulerCoreLoop,
    _resolve_poll_interval_seconds,
)
from tca.storage import (
    ChannelsRepository,
    ChannelStateRepository,
    PollJobsRepository,
    SettingsRepository,
    StorageRuntime,
    create_storage_runtime,
    dispose_storage_runtime,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


@pytest.fixture
async def settings_runtime(tmp_path: Path) -> AsyncIterator[StorageRuntime]:
    """Create runtime fixture with settings table for scheduler config."""
    db_path = tmp_path / "scheduler-jitter.sqlite3"
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


def test_next_run_at_within_jitter_bounds() -> None:
    """Ensure jittered next run stays within +/-20% interval bounds."""
    interval_seconds = 300
    jitter_ratio = 0.2
    last_success = datetime(2026, 2, 18, 12, 0, 0, tzinfo=UTC)
    rng = random.Random(1337)  # noqa: S311

    core_loop = SchedulerCoreLoop(
        channels_repository=cast("ChannelsRepository", object()),
        state_repository=cast("ChannelStateRepository", object()),
        jobs_repository=cast("PollJobsRepository", object()),
        poll_interval_seconds=interval_seconds,
        jitter_ratio=jitter_ratio,
        jitter_rng=rng,
    )

    next_run_at = core_loop._compute_next_run_at(state_last_success=last_success)  # noqa: SLF001
    lower_bound = last_success + timedelta(
        seconds=interval_seconds * (1 - jitter_ratio),
    )
    upper_bound = last_success + timedelta(
        seconds=interval_seconds * (1 + jitter_ratio),
    )
    if not (lower_bound <= next_run_at <= upper_bound):
        raise AssertionError


def test_jitter_is_deterministic_with_seeded_rng() -> None:
    """Ensure seeded RNG yields deterministic jitter results."""
    interval_seconds = 300
    last_success = datetime(2026, 2, 18, 12, 0, 0, tzinfo=UTC)

    rng_left = random.Random(2026)  # noqa: S311
    rng_right = random.Random(2026)  # noqa: S311
    left = SchedulerCoreLoop(
        channels_repository=cast("ChannelsRepository", object()),
        state_repository=cast("ChannelStateRepository", object()),
        jobs_repository=cast("PollJobsRepository", object()),
        poll_interval_seconds=interval_seconds,
        jitter_rng=rng_left,
    )
    right = SchedulerCoreLoop(
        channels_repository=cast("ChannelsRepository", object()),
        state_repository=cast("ChannelStateRepository", object()),
        jobs_repository=cast("PollJobsRepository", object()),
        poll_interval_seconds=interval_seconds,
        jitter_rng=rng_right,
    )

    left_next = left._compute_next_run_at(state_last_success=last_success)  # noqa: SLF001
    right_next = right._compute_next_run_at(state_last_success=last_success)  # noqa: SLF001
    if left_next != right_next:
        raise AssertionError


@pytest.mark.asyncio
async def test_poll_interval_resolves_from_settings(
    settings_runtime: StorageRuntime,
) -> None:
    """Ensure scheduler poll interval comes from dynamic settings."""
    repository = SettingsRepository(
        read_session_factory=settings_runtime.read_session_factory,
        write_session_factory=settings_runtime.write_session_factory,
    )
    await repository.create(
        key="scheduler.default_poll_interval_seconds",
        value=120,
    )

    resolved = await _resolve_poll_interval_seconds(
        repository=repository,
        default_value=DEFAULT_POLL_INTERVAL_SECONDS,
    )
    if resolved != 120:  # noqa: PLR2004
        raise AssertionError
