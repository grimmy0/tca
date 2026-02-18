"""Tests for Telegram flood wait handling using mocks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from telethon.errors import FloodWaitError  # pyright: ignore[reportMissingTypeStubs]

from tca.config.settings import load_settings
from tca.ingest import fetch_recent_messages, handle_flood_wait
from tca.ingest.account_risk import ACCOUNT_RISK_PAUSE_REASON, ACCOUNT_RISK_THRESHOLD
from tca.ingest.flood_wait import SIGNIFICANT_FLOOD_WAIT_SECONDS
from tca.storage import (
    AccountPauseRepository,
    ChannelsRepository,
    ChannelStateRepository,
    NotificationsRepository,
    SettingsRepository,
    StorageRuntime,
    create_storage_runtime,
    dispose_storage_runtime,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from pathlib import Path

    from tests.mocks.mock_telegram_client import MockTelegramClient


class RecordingWriterQueue:
    """Writer queue stub for flood-wait persistence tests."""

    submit_calls: int

    def __init__(self) -> None:
        """Initialize with zero submit calls."""
        self.submit_calls = 0

    async def submit(self, operation: Callable[[], Awaitable[object]]) -> object:
        """Record queue submissions and execute the operation inline."""
        self.submit_calls += 1
        return await operation()


@pytest.fixture
async def flood_wait_runtime(
    tmp_path: Path,
) -> AsyncIterator[
    tuple[
        ChannelStateRepository,
        ChannelsRepository,
        NotificationsRepository,
        SettingsRepository,
        AccountPauseRepository,
        StorageRuntime,
    ]
]:
    """Create repositories and schema fixture for flood wait tests."""
    db_path = tmp_path / "flood-wait.sqlite3"
    settings = load_settings({"TCA_DB_PATH": db_path.as_posix()})
    runtime = create_storage_runtime(settings)

    async with runtime.write_engine.begin() as connection:
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS telegram_accounts (
                id INTEGER PRIMARY KEY,
                api_id INTEGER NOT NULL,
                api_hash_encrypted BLOB NOT NULL,
                paused_at DATETIME NULL,
                pause_reason TEXT NULL,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
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
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
                channel_id INTEGER NOT NULL,
                cursor_json TEXT NULL,
                paused_until DATETIME NULL,
                last_success_at DATETIME NULL,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_channel_state_channel_id
                    FOREIGN KEY (channel_id)
                    REFERENCES telegram_channels(id)
                    ON DELETE CASCADE,
                CONSTRAINT pk_channel_state PRIMARY KEY (channel_id)
            )
            """,
        )
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
        yield (
            ChannelStateRepository(
                read_session_factory=runtime.read_session_factory,
                write_session_factory=runtime.write_session_factory,
            ),
            ChannelsRepository(
                read_session_factory=runtime.read_session_factory,
                write_session_factory=runtime.write_session_factory,
            ),
            NotificationsRepository(
                read_session_factory=runtime.read_session_factory,
                write_session_factory=runtime.write_session_factory,
            ),
            SettingsRepository(
                read_session_factory=runtime.read_session_factory,
                write_session_factory=runtime.write_session_factory,
            ),
            AccountPauseRepository(
                read_session_factory=runtime.read_session_factory,
                write_session_factory=runtime.write_session_factory,
            ),
            runtime,
        )
    finally:
        await dispose_storage_runtime(runtime)


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


@pytest.mark.asyncio
async def test_flood_wait_mock(mock_tg_client: MockTelegramClient) -> None:
    """Verify flood wait from message fetching surfaces through ingest service."""
    wait_seconds = 300
    exc = FloodWaitError(None)
    exc.seconds = wait_seconds
    mock_tg_client.responses["iter_messages"] = exc

    with pytest.raises(FloodWaitError) as excinfo:
        _ = await fetch_recent_messages(mock_tg_client, "channel://news", limit=5)

    if excinfo.value.seconds != wait_seconds:
        raise AssertionError
    if mock_tg_client.call_counts.get("iter_messages") != 1:
        raise AssertionError


@pytest.mark.asyncio
async def test_message_fetch_mock_returns_scripted_messages(
    mock_tg_client: MockTelegramClient,
) -> None:
    """Verify message fetch path returns deterministic scripted payload."""
    scripted_messages = [{"id": 1, "text": "a"}, {"id": 2, "text": "b"}]
    mock_tg_client.responses["iter_messages"] = scripted_messages

    result = await fetch_recent_messages(mock_tg_client, "channel://news", limit=2)

    if result != scripted_messages:
        raise AssertionError
    if mock_tg_client.call_counts.get("iter_messages") != 1:
        raise AssertionError


@pytest.mark.asyncio
async def test_flood_wait_marks_channel_paused_until_resume_time(
    flood_wait_runtime: tuple[
        ChannelStateRepository,
        ChannelsRepository,
        NotificationsRepository,
        SettingsRepository,
        AccountPauseRepository,
        StorageRuntime,
    ],
) -> None:
    """Ensure flood wait pauses channel until exact resume timestamp."""
    (
        state_repo,
        channels_repo,
        notifications_repo,
        _,
        _,
        runtime,
    ) = flood_wait_runtime
    await _seed_account(runtime, account_id=1)
    channel = await channels_repo.create_channel(
        account_id=1,
        telegram_channel_id=999,
        name="flood-channel",
        username=None,
    )

    queue = RecordingWriterQueue()
    now = datetime.now(UTC) + timedelta(hours=1)
    wait_seconds = 120
    exc = FloodWaitError(None)
    exc.seconds = wait_seconds

    record = await handle_flood_wait(
        writer_queue=queue,
        state_repository=state_repo,
        notifications_repository=notifications_repo,
        channel_id=channel.id,
        error=exc,
        time_provider=lambda: now,
    )

    expected_resume = now + timedelta(seconds=wait_seconds)
    if record.paused_until != expected_resume:
        raise AssertionError
    if queue.submit_calls != 1:
        raise AssertionError

    loaded = await state_repo.get_state(channel_id=channel.id)
    if loaded is None:
        raise AssertionError
    if loaded.paused_until != expected_resume:
        raise AssertionError


@pytest.mark.asyncio
async def test_flood_wait_emits_notification_for_significant_pause(
    flood_wait_runtime: tuple[
        ChannelStateRepository,
        ChannelsRepository,
        NotificationsRepository,
        SettingsRepository,
        AccountPauseRepository,
        StorageRuntime,
    ],
) -> None:
    """Ensure significant flood waits emit a notification."""
    (
        state_repo,
        channels_repo,
        notifications_repo,
        _,
        _,
        runtime,
    ) = flood_wait_runtime
    await _seed_account(runtime, account_id=2)
    channel = await channels_repo.create_channel(
        account_id=2,
        telegram_channel_id=1000,
        name="notify-channel",
        username=None,
    )

    queue = RecordingWriterQueue()
    now = datetime.now(UTC) + timedelta(hours=2)
    wait_seconds = SIGNIFICANT_FLOOD_WAIT_SECONDS + 5
    exc = FloodWaitError(None)
    exc.seconds = wait_seconds

    _ = await handle_flood_wait(
        writer_queue=queue,
        state_repository=state_repo,
        notifications_repository=notifications_repo,
        channel_id=channel.id,
        error=exc,
        time_provider=lambda: now,
    )

    notifications = await notifications_repo.list_notifications()
    if len(notifications) != 1:
        raise AssertionError
    record = notifications[0]
    if record.payload is None:
        raise AssertionError
    if record.payload.get("channel_id") != channel.id:
        raise AssertionError
    if record.payload.get("wait_seconds") != wait_seconds:
        raise AssertionError


@pytest.mark.asyncio
async def test_flood_wait_records_account_risk_breach(
    flood_wait_runtime: tuple[
        ChannelStateRepository,
        ChannelsRepository,
        NotificationsRepository,
        SettingsRepository,
        AccountPauseRepository,
        StorageRuntime,
    ],
) -> None:
    """Ensure repeated flood waits trigger account risk escalation."""
    (
        state_repo,
        channels_repo,
        notifications_repo,
        settings_repo,
        pause_repo,
        runtime,
    ) = flood_wait_runtime
    await _seed_account(runtime, account_id=3)
    channel = await channels_repo.create_channel(
        account_id=3,
        telegram_channel_id=2000,
        name="risk-channel",
        username=None,
    )

    queue = RecordingWriterQueue()
    now = datetime.now(UTC) + timedelta(hours=3)
    exc = FloodWaitError(None)
    exc.seconds = 30

    for idx in range(ACCOUNT_RISK_THRESHOLD):
        _ = await handle_flood_wait(
            writer_queue=queue,
            state_repository=state_repo,
            notifications_repository=notifications_repo,
            settings_repository=settings_repo,
            pause_repository=pause_repo,
            account_id=channel.account_id,
            channel_id=channel.id,
            error=exc,
            time_provider=lambda offset=idx: now + timedelta(minutes=offset),
        )

    pause_state = await pause_repo.get_pause_state(account_id=channel.account_id)
    if pause_state is None or pause_state.paused_at is None:
        raise AssertionError
    if pause_state.pause_reason != ACCOUNT_RISK_PAUSE_REASON:
        raise AssertionError

    notifications = await notifications_repo.list_notifications()
    if len(notifications) != 1:
        raise AssertionError
