"""Tests for channel-groups repository CRUD and membership constraints."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tca.config.settings import load_settings
from tca.storage import (
    ChannelAlreadyAssignedToGroupError,
    ChannelGroupRecord,
    ChannelGroupsRepository,
    StorageRuntime,
    create_storage_runtime,
    dispose_storage_runtime,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

DEFAULT_ACCOUNT_ID = 1
INITIAL_GROUP_HORIZON_MINUTES = 90
UPDATED_GROUP_HORIZON_MINUTES = 120
PRIMARY_CHANNEL_ID = 1
SECONDARY_CHANNEL_ID = 2


@pytest.fixture
async def repository_runtime(
    tmp_path: Path,
) -> AsyncIterator[tuple[ChannelGroupsRepository, StorageRuntime]]:
    """Create repository and schema fixture for channel-group storage tests."""
    db_path = tmp_path / "channel-groups-repository.sqlite3"
    settings = load_settings({"TCA_DB_PATH": db_path.as_posix()})
    runtime = create_storage_runtime(settings)

    async with runtime.write_engine.begin() as connection:
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS telegram_accounts (
                id INTEGER PRIMARY KEY,
                api_id INTEGER NOT NULL,
                api_hash_encrypted BLOB NOT NULL
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
            CREATE TABLE IF NOT EXISTS channel_groups (
                id INTEGER PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                description TEXT NULL,
                dedupe_horizon_minutes_override INTEGER NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
        )
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS channel_group_members (
                group_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_channel_group_members_group_id
                    FOREIGN KEY (group_id)
                    REFERENCES channel_groups(id)
                    ON DELETE CASCADE,
                CONSTRAINT fk_channel_group_members_channel_id
                    FOREIGN KEY (channel_id)
                    REFERENCES telegram_channels(id)
                    ON DELETE CASCADE,
                CONSTRAINT pk_channel_group_members
                    PRIMARY KEY (group_id, channel_id),
                CONSTRAINT uq_channel_group_members_channel_id
                    UNIQUE (channel_id)
            )
            """,
        )

    async with runtime.write_session_factory() as session:
        _ = await session.execute(
            text(
                """
                INSERT INTO telegram_accounts (id, api_id, api_hash_encrypted)
                VALUES (:id, :api_id, :api_hash_encrypted)
                """,
            ),
            {
                "id": DEFAULT_ACCOUNT_ID,
                "api_id": 12345,
                "api_hash_encrypted": b"encrypted-api-hash",
            },
        )
        await session.commit()

    try:
        yield (
            ChannelGroupsRepository(
                read_session_factory=runtime.read_session_factory,
                write_session_factory=runtime.write_session_factory,
            ),
            runtime,
        )
    finally:
        await dispose_storage_runtime(runtime)


@pytest.mark.asyncio
async def test_group_create_update_delete_works(
    repository_runtime: tuple[ChannelGroupsRepository, StorageRuntime],
) -> None:
    """Ensure group repository supports create, update, and delete operations."""
    repository, _ = repository_runtime
    created = await repository.create_group(
        name="Priority Sources",
        description="High-signal channels",
        dedupe_horizon_minutes_override=INITIAL_GROUP_HORIZON_MINUTES,
    )
    _assert_group_values(
        created,
        expected_name="Priority Sources",
        expected_description="High-signal channels",
        expected_horizon_minutes=INITIAL_GROUP_HORIZON_MINUTES,
    )

    updated = await repository.update_group(
        group_id=created.id,
        name="Updated Sources",
        description=None,
        dedupe_horizon_minutes_override=UPDATED_GROUP_HORIZON_MINUTES,
    )
    if updated is None:
        raise AssertionError
    _assert_group_values(
        updated,
        expected_name="Updated Sources",
        expected_description=None,
        expected_horizon_minutes=UPDATED_GROUP_HORIZON_MINUTES,
    )

    deleted = await repository.delete_group(group_id=created.id)
    if not deleted:
        raise AssertionError
    loaded = await repository.get_group_by_id(group_id=created.id)
    if loaded is not None:
        raise AssertionError


@pytest.mark.asyncio
async def test_channel_cannot_be_assigned_to_multiple_groups(
    repository_runtime: tuple[ChannelGroupsRepository, StorageRuntime],
) -> None:
    """Ensure one channel can belong to at most one channel group."""
    repository, runtime = repository_runtime
    first_group = await repository.create_group(
        name="Group A",
        description=None,
        dedupe_horizon_minutes_override=None,
    )
    second_group = await repository.create_group(
        name="Group B",
        description=None,
        dedupe_horizon_minutes_override=None,
    )
    await _insert_channel(
        runtime,
        channel_id=PRIMARY_CHANNEL_ID,
        telegram_channel_id=10001,
        name="alpha",
    )

    assigned = await repository.add_channel_membership(
        group_id=first_group.id,
        channel_id=PRIMARY_CHANNEL_ID,
    )
    if assigned.group_id != first_group.id:
        raise AssertionError
    if assigned.channel_id != PRIMARY_CHANNEL_ID:
        raise AssertionError

    with pytest.raises(ChannelAlreadyAssignedToGroupError) as exc_info:
        _ = await repository.add_channel_membership(
            group_id=second_group.id,
            channel_id=PRIMARY_CHANNEL_ID,
        )
    if (
        str(exc_info.value)
        != f"Channel '{PRIMARY_CHANNEL_ID}' is already assigned to a group."
    ):
        raise AssertionError

    current = await repository.get_membership_by_channel_id(
        channel_id=PRIMARY_CHANNEL_ID,
    )
    if current is None:
        raise AssertionError
    if current.group_id != first_group.id:
        raise AssertionError


@pytest.mark.asyncio
async def test_removing_group_cleans_memberships(
    repository_runtime: tuple[ChannelGroupsRepository, StorageRuntime],
) -> None:
    """Ensure deleting a group removes memberships via FK cascade semantics."""
    repository, runtime = repository_runtime
    group = await repository.create_group(
        name="Transient Group",
        description="To be removed",
        dedupe_horizon_minutes_override=30,
    )
    await _insert_channel(
        runtime,
        channel_id=SECONDARY_CHANNEL_ID,
        telegram_channel_id=10002,
        name="beta",
    )
    _ = await repository.add_channel_membership(
        group_id=group.id,
        channel_id=SECONDARY_CHANNEL_ID,
    )

    deleted = await repository.delete_group(group_id=group.id)
    if not deleted:
        raise AssertionError
    membership = await repository.get_membership_by_channel_id(
        channel_id=SECONDARY_CHANNEL_ID,
    )
    if membership is not None:
        raise AssertionError


@pytest.mark.asyncio
async def test_remove_channel_membership_works_and_is_idempotent(
    repository_runtime: tuple[ChannelGroupsRepository, StorageRuntime],
) -> None:
    """Ensure membership remove reports existence and supports repeated calls."""
    repository, runtime = repository_runtime
    group = await repository.create_group(
        name="Removable Membership Group",
        description=None,
        dedupe_horizon_minutes_override=None,
    )
    await _insert_channel(
        runtime,
        channel_id=SECONDARY_CHANNEL_ID,
        telegram_channel_id=10003,
        name="gamma",
    )
    _ = await repository.add_channel_membership(
        group_id=group.id,
        channel_id=SECONDARY_CHANNEL_ID,
    )

    removed = await repository.remove_channel_membership(
        group_id=group.id,
        channel_id=SECONDARY_CHANNEL_ID,
    )
    if not removed:
        raise AssertionError
    membership = await repository.get_membership_by_channel_id(
        channel_id=SECONDARY_CHANNEL_ID,
    )
    if membership is not None:
        raise AssertionError

    removed_again = await repository.remove_channel_membership(
        group_id=group.id,
        channel_id=SECONDARY_CHANNEL_ID,
    )
    if removed_again:
        raise AssertionError


@pytest.mark.asyncio
async def test_add_channel_membership_surfaces_foreign_key_integrity_errors(
    repository_runtime: tuple[ChannelGroupsRepository, StorageRuntime],
) -> None:
    """Ensure FK integrity failures are not remapped as duplicate assignments."""
    repository, _ = repository_runtime
    group = await repository.create_group(
        name="FK Validation Group",
        description=None,
        dedupe_horizon_minutes_override=None,
    )

    with pytest.raises(IntegrityError):
        _ = await repository.add_channel_membership(
            group_id=group.id,
            channel_id=9999,
        )


async def _insert_channel(
    runtime: StorageRuntime,
    *,
    channel_id: int,
    telegram_channel_id: int,
    name: str,
) -> None:
    """Insert a channel fixture row with deterministic primary key value."""
    statement = text(
        """
        INSERT INTO telegram_channels (
            id,
            account_id,
            telegram_channel_id,
            name
        )
        VALUES (
            :id,
            :account_id,
            :telegram_channel_id,
            :name
        )
        """,
    )
    async with runtime.write_session_factory() as session:
        _ = await session.execute(
            statement,
            {
                "id": channel_id,
                "account_id": DEFAULT_ACCOUNT_ID,
                "telegram_channel_id": telegram_channel_id,
                "name": name,
            },
        )
        await session.commit()


def _assert_group_values(
    record: ChannelGroupRecord,
    *,
    expected_name: str,
    expected_description: str | None,
    expected_horizon_minutes: int | None,
) -> None:
    if type(record.id) is not int:
        raise AssertionError
    if record.name != expected_name:
        raise AssertionError
    if record.description != expected_description:
        raise AssertionError
    if record.dedupe_horizon_minutes_override != expected_horizon_minutes:
        raise AssertionError
