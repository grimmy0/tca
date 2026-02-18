"""Tests for cluster creation and member insertion flows."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy import text

from tca.config.settings import load_settings
from tca.storage import (
    DedupeClustersRepository,
    StorageRuntime,
    create_storage_runtime,
    dispose_storage_runtime,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


@pytest.fixture
async def cluster_repository(
    tmp_path: Path,
) -> AsyncIterator[tuple[DedupeClustersRepository, StorageRuntime]]:
    """Build repository and minimal schema for cluster assignment tests."""
    db_path = tmp_path / "cluster-create.sqlite3"
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
                    ON DELETE CASCADE
            )
            """,
        )
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL,
                message_id BIGINT NOT NULL,
                raw_message_id INTEGER NULL,
                published_at DATETIME NULL,
                title TEXT NULL,
                body TEXT NULL,
                canonical_url TEXT NULL,
                canonical_url_hash VARCHAR(64) NULL,
                content_hash VARCHAR(64) NULL,
                dedupe_state VARCHAR(32) NOT NULL DEFAULT 'pending',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_items_channel_id
                    FOREIGN KEY (channel_id)
                    REFERENCES telegram_channels(id)
                    ON DELETE CASCADE,
                CONSTRAINT uq_items_channel_id_message_id
                    UNIQUE (channel_id, message_id)
            )
            """,
        )
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS dedupe_clusters (
                id INTEGER PRIMARY KEY,
                cluster_key VARCHAR(36) NOT NULL,
                representative_item_id INTEGER NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_dedupe_clusters_representative_item_id
                    FOREIGN KEY (representative_item_id)
                    REFERENCES items(id)
                    ON DELETE SET NULL,
                CONSTRAINT uq_dedupe_clusters_cluster_key
                    UNIQUE (cluster_key)
            )
            """,
        )
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS dedupe_members (
                cluster_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_dedupe_members_cluster_id
                    FOREIGN KEY (cluster_id)
                    REFERENCES dedupe_clusters(id)
                    ON DELETE CASCADE,
                CONSTRAINT fk_dedupe_members_item_id
                    FOREIGN KEY (item_id)
                    REFERENCES items(id)
                    ON DELETE CASCADE,
                CONSTRAINT pk_dedupe_members
                    PRIMARY KEY (cluster_id, item_id)
            )
            """,
        )

    await _insert_channel_fixtures(runtime)

    try:
        yield (
            DedupeClustersRepository(
                read_session_factory=runtime.read_session_factory,
                write_session_factory=runtime.write_session_factory,
            ),
            runtime,
        )
    finally:
        await dispose_storage_runtime(runtime)


@pytest.mark.asyncio
async def test_new_item_without_match_creates_exactly_one_cluster(
    cluster_repository: tuple[DedupeClustersRepository, StorageRuntime],
) -> None:
    """Unmatched item should create one cluster and one membership row."""
    repository, runtime = cluster_repository
    await _insert_item(runtime=runtime, item_id=101, channel_id=11, message_id=101)

    assignment = await repository.assign_item_to_cluster(
        item_id=101,
        matched_cluster_id=None,
    )

    if not assignment.created_cluster:
        raise AssertionError
    if not assignment.created_membership:
        raise AssertionError

    async with runtime.read_session_factory() as session:
        cluster_count = cast(
            "int",
            (
                await session.execute(
                    text("SELECT COUNT(*) FROM dedupe_clusters"),
                )
            ).scalar_one(),
        )

    if cluster_count != 1:
        raise AssertionError


@pytest.mark.asyncio
async def test_membership_row_created_for_each_item_cluster_link(
    cluster_repository: tuple[DedupeClustersRepository, StorageRuntime],
) -> None:
    """Both unmatched and matched flows should produce membership links."""
    repository, runtime = cluster_repository
    await _insert_item(runtime=runtime, item_id=101, channel_id=11, message_id=101)
    await _insert_item(runtime=runtime, item_id=102, channel_id=11, message_id=102)

    created = await repository.assign_item_to_cluster(
        item_id=101,
        matched_cluster_id=None,
    )
    _ = await repository.assign_item_to_cluster(
        item_id=102,
        matched_cluster_id=created.cluster_id,
    )

    async with runtime.read_session_factory() as session:
        result = await session.execute(
            text(
                """
                SELECT item_id
                FROM dedupe_members
                WHERE cluster_id = :cluster_id
                ORDER BY item_id ASC
                """,
            ),
            {"cluster_id": created.cluster_id},
        )
        item_ids = [cast("int", row.item_id) for row in result]

    if item_ids != [101, 102]:
        raise AssertionError


@pytest.mark.asyncio
async def test_duplicate_membership_insertion_is_prevented(
    cluster_repository: tuple[DedupeClustersRepository, StorageRuntime],
) -> None:
    """Repeated item-to-cluster assignment should not duplicate join rows."""
    repository, runtime = cluster_repository
    await _insert_item(runtime=runtime, item_id=101, channel_id=11, message_id=101)
    await _insert_item(runtime=runtime, item_id=102, channel_id=11, message_id=102)

    created = await repository.assign_item_to_cluster(
        item_id=101,
        matched_cluster_id=None,
    )
    first = await repository.assign_item_to_cluster(
        item_id=102,
        matched_cluster_id=created.cluster_id,
    )
    second = await repository.assign_item_to_cluster(
        item_id=102,
        matched_cluster_id=created.cluster_id,
    )

    if not first.created_membership:
        raise AssertionError
    if second.created_membership:
        raise AssertionError

    async with runtime.read_session_factory() as session:
        count = cast(
            "int",
            (
                await session.execute(
                    text(
                        """
                        SELECT COUNT(*)
                        FROM dedupe_members
                        WHERE cluster_id = :cluster_id
                          AND item_id = :item_id
                        """,
                    ),
                    {"cluster_id": created.cluster_id, "item_id": 102},
                )
            ).scalar_one(),
        )

    if count != 1:
        raise AssertionError


async def _insert_channel_fixtures(runtime: StorageRuntime) -> None:
    """Insert one account and one channel required by item FKs."""
    async with runtime.write_session_factory() as session:
        _ = await session.execute(
            text(
                """
                INSERT INTO telegram_accounts (id, api_id, api_hash_encrypted)
                VALUES (:id, :api_id, :api_hash_encrypted)
                """,
            ),
            {
                "id": 1,
                "api_id": 12345,
                "api_hash_encrypted": b"encrypted-api-hash",
            },
        )
        _ = await session.execute(
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
                VALUES (
                    :id,
                    :account_id,
                    :telegram_channel_id,
                    :name,
                    :username,
                    :is_enabled
                )
                """,
            ),
            {
                "id": 11,
                "account_id": 1,
                "telegram_channel_id": 20001,
                "name": "cluster-create-channel",
                "username": None,
                "is_enabled": True,
            },
        )
        await session.commit()


async def _insert_item(
    *,
    runtime: StorageRuntime,
    item_id: int,
    channel_id: int,
    message_id: int,
) -> None:
    """Insert one item row for dedupe membership linking."""
    async with runtime.write_session_factory() as session:
        _ = await session.execute(
            text(
                """
                INSERT INTO items (id, channel_id, message_id)
                VALUES (:id, :channel_id, :message_id)
                """,
            ),
            {"id": item_id, "channel_id": channel_id, "message_id": message_id},
        )
        await session.commit()
