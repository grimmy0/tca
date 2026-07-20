"""Tests for BotDeliveriesRepository."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from tca.config.settings import load_settings
from tca.storage import (
    BotDeliveriesRepository,
    BotDeliveryAlreadyExistsError,
    create_storage_runtime,
    dispose_storage_runtime,
    run_startup_migrations,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from tca.storage.db import StorageRuntime


@pytest.fixture
async def storage_runtime(tmp_path: Path) -> AsyncIterator[StorageRuntime]:
    """Create initialized SQLite database runtime using standard startup migrations."""
    db_path = tmp_path / "bot-deliveries-test.sqlite3"
    os.environ["TCA_DB_PATH"] = db_path.as_posix()
    run_startup_migrations()

    settings = load_settings({"TCA_DB_PATH": db_path.as_posix()})
    runtime = create_storage_runtime(settings)
    try:
        yield runtime
    finally:
        await dispose_storage_runtime(runtime)
        os.environ.pop("TCA_DB_PATH", None)


@pytest.fixture
def repository(storage_runtime: StorageRuntime) -> BotDeliveriesRepository:
    """Create a BotDeliveriesRepository instance."""
    return BotDeliveriesRepository(
        read_session_factory=storage_runtime.read_session_factory,
        write_session_factory=storage_runtime.write_session_factory,
    )


async def _seed_data(
    runtime: StorageRuntime,
) -> tuple[int, int, int]:
    """Seed test channel, items, and clusters, returning (channel_id, item_id, cluster_id)."""
    async with runtime.write_session_factory() as session:
        # 1. Insert telegram account
        from sqlalchemy import text
        res = await session.execute(
            text(
                """
                INSERT INTO telegram_accounts (api_id, api_hash_encrypted)
                VALUES (12345, x'aabbcc')
                RETURNING id
                """
            )
        )
        account_id = res.scalar_one()

        # 2. Insert telegram channel
        res = await session.execute(
            text(
                """
                INSERT INTO telegram_channels (account_id, telegram_channel_id, name, username, is_enabled)
                VALUES (:account_id, 99999, 'Test Channel', 'test_channel', 1)
                RETURNING id
                """
            ),
            {"account_id": account_id},
        )
        channel_id = res.scalar_one()

        # 3. Insert items
        res = await session.execute(
            text(
                """
                INSERT INTO items (channel_id, message_id, title, body, canonical_url, published_at, dedupe_state)
                VALUES (:channel_id, 100, 'Title 1', 'Body 1', 'https://example.com/1', '2026-07-20T00:00:00Z', 'processed')
                RETURNING id
                """
            ),
            {"channel_id": channel_id},
        )
        item1_id = res.scalar_one()

        res = await session.execute(
            text(
                """
                INSERT INTO items (channel_id, message_id, title, body, canonical_url, published_at, dedupe_state)
                VALUES (:channel_id, 101, 'Title 2', 'Body 2', 'https://example.com/2', '2026-07-20T00:00:01Z', 'processed')
                RETURNING id
                """
            ),
            {"channel_id": channel_id},
        )
        item2_id = res.scalar_one()

        # 4. Insert cluster
        res = await session.execute(
            text(
                """
                INSERT INTO dedupe_clusters (cluster_key, representative_item_id)
                VALUES ('cluster-key-1', :item_id)
                RETURNING id
                """
            ),
            {"item_id": item1_id},
        )
        cluster_id = res.scalar_one()

        # 5. Link items to cluster in dedupe_members
        await session.execute(
            text(
                """
                INSERT INTO dedupe_members (cluster_id, item_id)
                VALUES (:cluster_id, :item1_id), (:cluster_id, :item2_id)
                """
            ),
            {"cluster_id": cluster_id, "item1_id": item1_id, "item2_id": item2_id},
        )

        await session.commit()

    return channel_id, item1_id, cluster_id


@pytest.mark.asyncio
async def test_record_delivery_success(
    storage_runtime: StorageRuntime,
    repository: BotDeliveriesRepository,
) -> None:
    """Ensure we can record a cluster delivery successfully."""
    _, _, cluster_id = await _seed_data(storage_runtime)

    # Initially not delivered
    is_delivered = await repository.has_been_delivered(cluster_id)
    if is_delivered:
        raise AssertionError("Expected cluster not to be delivered yet.")

    # Record delivery
    record = await repository.record_delivery(
        cluster_id=cluster_id,
        telegram_message_id="msg-12345",
    )

    if record.cluster_id != cluster_id:
        raise AssertionError
    if record.telegram_message_id != "msg-12345":
        raise AssertionError
    if not isinstance(record.delivered_at, datetime):
        raise AssertionError
    if record.delivery_id <= 0:
        raise AssertionError

    # Check after delivery
    is_delivered = await repository.has_been_delivered(cluster_id)
    if not is_delivered:
        raise AssertionError("Expected cluster to be delivered.")


@pytest.mark.asyncio
async def test_record_delivery_duplicate_raises_error(
    storage_runtime: StorageRuntime,
    repository: BotDeliveriesRepository,
) -> None:
    """Ensure recording the same cluster twice raises BotDeliveryAlreadyExistsError."""
    _, _, cluster_id = await _seed_data(storage_runtime)

    # First recording is successful
    _ = await repository.record_delivery(cluster_id=cluster_id)

    # Second recording raises error
    with pytest.raises(BotDeliveryAlreadyExistsError) as exc_info:
        await repository.record_delivery(cluster_id=cluster_id)

    if f"cluster_id {cluster_id}" not in str(exc_info.value):
        raise AssertionError


@pytest.mark.asyncio
async def test_list_undelivered_entries(
    storage_runtime: StorageRuntime,
    repository: BotDeliveriesRepository,
) -> None:
    """Ensure list_undelivered_entries returns expected undelivered records only."""
    channel_id, item1_id, cluster1_id = await _seed_data(storage_runtime)

    # Seed a second cluster
    async with storage_runtime.write_session_factory() as session:
        from sqlalchemy import text
        res = await session.execute(
            text(
                """
                INSERT INTO items (channel_id, message_id, title, body, canonical_url, published_at, dedupe_state)
                VALUES (:channel_id, 102, 'Title 3', 'Body 3', 'https://example.com/3', '2026-07-20T00:00:02Z', 'processed')
                RETURNING id
                """
            ),
            {"channel_id": channel_id},
        )
        item3_id = res.scalar_one()

        res = await session.execute(
            text(
                """
                INSERT INTO dedupe_clusters (cluster_key, representative_item_id)
                VALUES ('cluster-key-2', :item_id)
                RETURNING id
                """
            ),
            {"item_id": item3_id},
        )
        cluster2_id = res.scalar_one()

        await session.execute(
            text(
                """
                INSERT INTO dedupe_members (cluster_id, item_id)
                VALUES (:cluster_id, :item_id)
                """
            ),
            {"cluster_id": cluster2_id, "item_id": item3_id},
        )
        await session.commit()

    # Both clusters are undelivered
    entries = await repository.list_undelivered_entries(limit=10)
    if len(entries) != 2:
        raise AssertionError

    # Order must be by cluster_id ASC
    if entries[0].cluster_id != cluster1_id or entries[1].cluster_id != cluster2_id:
        raise AssertionError

    # Check fields of cluster1 (which has 2 duplicate members)
    entry1 = entries[0]
    if entry1.representative_title != "Title 1":
        raise AssertionError
    if entry1.representative_body != "Body 1":
        raise AssertionError
    if entry1.representative_canonical_url != "https://example.com/1":
        raise AssertionError
    if entry1.representative_published_at != datetime(2026, 7, 20, 0, 0, 0, tzinfo=UTC):
        raise AssertionError
    if entry1.channel_name != "Test Channel":
        raise AssertionError
    if entry1.channel_username != "test_channel":
        raise AssertionError
    if entry1.duplicate_count != 2:
        raise AssertionError

    # Deliver cluster 1
    _ = await repository.record_delivery(cluster_id=cluster1_id)

    # Now only cluster 2 is undelivered
    entries = await repository.list_undelivered_entries(limit=10)
    if len(entries) != 1:
        raise AssertionError
    if entries[0].cluster_id != cluster2_id:
        raise AssertionError
