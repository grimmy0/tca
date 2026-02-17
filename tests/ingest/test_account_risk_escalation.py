"""Tests for account risk escalation behavior."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from tca.config.settings import load_settings
from tca.ingest.account_risk import (
    ACCOUNT_RISK_NOTIFICATION_SEVERITY,
    ACCOUNT_RISK_NOTIFICATION_TYPE,
    ACCOUNT_RISK_PAUSE_REASON,
    ACCOUNT_RISK_THRESHOLD,
    record_account_risk_breach,
)
from tca.storage import (
    AccountPauseRepository,
    ChannelsRepository,
    NotificationsRepository,
    SettingsRepository,
    StorageRuntime,
    create_storage_runtime,
    dispose_storage_runtime,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from pathlib import Path


class RecordingWriterQueue:
    """Writer queue stub for risk escalation persistence tests."""

    submit_calls: int

    def __init__(self) -> None:
        self.submit_calls = 0

    async def submit(self, operation: Callable[[], Awaitable[object]]) -> object:
        """Record queue submissions and execute the operation inline."""
        self.submit_calls += 1
        return await operation()


@pytest.fixture
async def account_risk_runtime(
    tmp_path: Path,
) -> AsyncIterator[
    tuple[
        AccountPauseRepository,
        ChannelsRepository,
        SettingsRepository,
        NotificationsRepository,
        StorageRuntime,
    ]
]:
    """Create repositories and schema fixture for account risk tests."""
    db_path = tmp_path / "account-risk.sqlite3"
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
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY,
                key VARCHAR(255) NOT NULL,
                value_json TEXT NOT NULL,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_settings_key UNIQUE (key)
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

    try:
        yield (
            AccountPauseRepository(
                read_session_factory=runtime.read_session_factory,
                write_session_factory=runtime.write_session_factory,
            ),
            ChannelsRepository(
                read_session_factory=runtime.read_session_factory,
                write_session_factory=runtime.write_session_factory,
            ),
            SettingsRepository(
                read_session_factory=runtime.read_session_factory,
                write_session_factory=runtime.write_session_factory,
            ),
            NotificationsRepository(
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


async def _seed_channel(
    runtime: StorageRuntime,
    *,
    channel_id: int,
    account_id: int,
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
                    username,
                    is_enabled
                )
                VALUES (:id, :account_id, :telegram_channel_id, :name, :username, 1)
                """,
            ),
            {
                "id": channel_id,
                "account_id": account_id,
                "telegram_channel_id": 8000 + channel_id,
                "name": f"channel-{channel_id}",
                "username": None,
            },
        )
        await session.commit()


@pytest.mark.asyncio
async def test_account_risk_escalation_pauses_account_on_repeated_breaches(
    account_risk_runtime: tuple[
        AccountPauseRepository,
        ChannelsRepository,
        SettingsRepository,
        NotificationsRepository,
        StorageRuntime,
    ],
) -> None:
    """Ensure repeated breaches pause the account with explicit reason."""
    pause_repo, _, settings_repo, notifications_repo, runtime = account_risk_runtime
    await _seed_account(runtime, account_id=7)

    queue = RecordingWriterQueue()
    now = datetime(2026, 2, 22, 10, 0, tzinfo=timezone.utc)

    for idx in range(ACCOUNT_RISK_THRESHOLD - 1):
        paused = await record_account_risk_breach(
            writer_queue=queue,
            settings_repository=settings_repo,
            pause_repository=pause_repo,
            notifications_repository=notifications_repo,
            account_id=7,
            breach_reason="flood-wait",
            time_provider=lambda offset=idx: now + timedelta(minutes=offset),
        )
        if paused is not None:
            raise AssertionError

    paused = await record_account_risk_breach(
        writer_queue=queue,
        settings_repository=settings_repo,
        pause_repository=pause_repo,
        notifications_repository=notifications_repo,
        account_id=7,
        breach_reason="flood-wait",
        time_provider=lambda: now + timedelta(minutes=ACCOUNT_RISK_THRESHOLD),
    )
    if paused is None:
        raise AssertionError
    if paused.pause_reason != ACCOUNT_RISK_PAUSE_REASON:
        raise AssertionError
    if paused.paused_at is None:
        raise AssertionError


@pytest.mark.asyncio
async def test_account_risk_escalation_emits_notification_once(
    account_risk_runtime: tuple[
        AccountPauseRepository,
        ChannelsRepository,
        SettingsRepository,
        NotificationsRepository,
        StorageRuntime,
    ],
) -> None:
    """Ensure notification is high severity and emitted once per pause."""
    pause_repo, _, settings_repo, notifications_repo, runtime = account_risk_runtime
    await _seed_account(runtime, account_id=9)

    queue = RecordingWriterQueue()
    now = datetime(2026, 2, 22, 11, 0, tzinfo=timezone.utc)

    for idx in range(ACCOUNT_RISK_THRESHOLD):
        _ = await record_account_risk_breach(
            writer_queue=queue,
            settings_repository=settings_repo,
            pause_repository=pause_repo,
            notifications_repository=notifications_repo,
            account_id=9,
            breach_reason="auth-failure",
            time_provider=lambda offset=idx: now + timedelta(minutes=offset),
        )

    notifications = await notifications_repo.list_notifications()
    if len(notifications) != 1:
        raise AssertionError
    notification = notifications[0]
    if notification.type != ACCOUNT_RISK_NOTIFICATION_TYPE:
        raise AssertionError
    if notification.severity != ACCOUNT_RISK_NOTIFICATION_SEVERITY:
        raise AssertionError

    _ = await record_account_risk_breach(
        writer_queue=queue,
        settings_repository=settings_repo,
        pause_repository=pause_repo,
        notifications_repository=notifications_repo,
        account_id=9,
        breach_reason="auth-failure",
        time_provider=lambda: now + timedelta(minutes=ACCOUNT_RISK_THRESHOLD + 5),
    )

    notifications = await notifications_repo.list_notifications()
    if len(notifications) != 1:
        raise AssertionError


@pytest.mark.asyncio
async def test_account_risk_escalation_blocks_schedulable_channels_until_resume(
    account_risk_runtime: tuple[
        AccountPauseRepository,
        ChannelsRepository,
        SettingsRepository,
        NotificationsRepository,
        StorageRuntime,
    ],
) -> None:
    """Ensure paused accounts block polling until explicitly resumed."""
    pause_repo, channels_repo, settings_repo, notifications_repo, runtime = (
        account_risk_runtime
    )
    await _seed_account(runtime, account_id=11)
    await _seed_channel(runtime, channel_id=501, account_id=11)

    schedulable = await channels_repo.list_schedulable_channels()
    if len(schedulable) != 1:
        raise AssertionError

    queue = RecordingWriterQueue()
    now = datetime(2026, 2, 22, 12, 0, tzinfo=timezone.utc)

    for idx in range(ACCOUNT_RISK_THRESHOLD):
        _ = await record_account_risk_breach(
            writer_queue=queue,
            settings_repository=settings_repo,
            pause_repository=pause_repo,
            notifications_repository=notifications_repo,
            account_id=11,
            breach_reason="flood-wait",
            time_provider=lambda offset=idx: now + timedelta(minutes=offset),
        )

    schedulable = await channels_repo.list_schedulable_channels()
    if schedulable:
        raise AssertionError

    resumed = await pause_repo.resume_account(account_id=11)
    if resumed is None or resumed.paused_at is not None:
        raise AssertionError

    schedulable = await channels_repo.list_schedulable_channels()
    if len(schedulable) != 1:
        raise AssertionError
