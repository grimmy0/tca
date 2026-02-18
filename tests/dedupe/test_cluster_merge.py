"""Tests for cluster merge flow and event persistence."""

from __future__ import annotations

from json import loads
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


EXPECTED_REMOVED_SOURCE_CLUSTERS = 2


@pytest.fixture
async def cluster_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[DedupeClustersRepository, StorageRuntime]]:
    """Build repository and schema for cluster merge tests."""
    db_path = tmp_path / "cluster-merge.sqlite3"
    monkeypatch.setenv("TCA_DB_PATH", db_path.as_posix())
    settings = load_settings()
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
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS dedupe_decisions (
                id INTEGER PRIMARY KEY,
                item_id INTEGER NOT NULL,
                cluster_id INTEGER NULL,
                candidate_item_id INTEGER NULL,
                strategy_name VARCHAR(64) NOT NULL,
                outcome VARCHAR(32) NOT NULL,
                reason_code VARCHAR(128) NULL,
                score FLOAT NULL,
                metadata_json TEXT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT fk_dedupe_decisions_item_id
                    FOREIGN KEY (item_id)
                    REFERENCES items(id)
                    ON DELETE CASCADE,
                CONSTRAINT fk_dedupe_decisions_cluster_id
                    FOREIGN KEY (cluster_id)
                    REFERENCES dedupe_clusters(id)
                    ON DELETE SET NULL,
                CONSTRAINT fk_dedupe_decisions_candidate_item_id
                    FOREIGN KEY (candidate_item_id)
                    REFERENCES items(id)
                    ON DELETE SET NULL
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
async def test_merge_moves_all_members_to_smallest_target_cluster(
    cluster_repository: tuple[DedupeClustersRepository, StorageRuntime],
) -> None:
    """All source members should move to the smallest cluster id target."""
    repository, runtime = cluster_repository
    await _seed_merge_fixtures(runtime=runtime)

    result = await repository.merge_clusters(matched_cluster_ids=[2, 1, 3])

    if result.target_cluster_id != 1:
        raise AssertionError
    if result.source_cluster_ids != (2, 3):
        raise AssertionError

    async with runtime.read_session_factory() as session:
        rows = await session.execute(
            text(
                """
                SELECT cluster_id, item_id
                FROM dedupe_members
                ORDER BY cluster_id ASC, item_id ASC
                """,
            ),
        )
        members = [
            (cast("int", row.cluster_id), cast("int", row.item_id)) for row in rows
        ]

    if members != [(1, 101), (1, 102), (1, 103), (1, 104), (1, 105)]:
        raise AssertionError


@pytest.mark.asyncio
async def test_merge_removes_source_clusters_per_schema(
    cluster_repository: tuple[DedupeClustersRepository, StorageRuntime],
) -> None:
    """Source clusters should be removed after merge in current schema."""
    repository, runtime = cluster_repository
    await _seed_merge_fixtures(runtime=runtime)

    result = await repository.merge_clusters(matched_cluster_ids=[3, 1, 2])

    if result.removed_cluster_count != EXPECTED_REMOVED_SOURCE_CLUSTERS:
        raise AssertionError

    async with runtime.read_session_factory() as session:
        rows = await session.execute(
            text(
                """
                SELECT id
                FROM dedupe_clusters
                ORDER BY id ASC
                """,
            ),
        )
        cluster_ids = [cast("int", row.id) for row in rows]

    if cluster_ids != [1]:
        raise AssertionError


@pytest.mark.asyncio
async def test_merge_records_cluster_merge_decision_event(
    cluster_repository: tuple[DedupeClustersRepository, StorageRuntime],
) -> None:
    """Merge flow should persist a cluster_merge decision event."""
    repository, runtime = cluster_repository
    await _seed_merge_fixtures(runtime=runtime)

    result = await repository.merge_clusters(matched_cluster_ids=[1, 2, 3])

    if not result.recorded_event:
        raise AssertionError

    async with runtime.read_session_factory() as session:
        row = (
            (
                await session.execute(
                    text(
                        """
                        SELECT
                            item_id,
                            cluster_id,
                            strategy_name,
                            outcome,
                            reason_code,
                            metadata_json
                        FROM dedupe_decisions
                        ORDER BY id DESC
                        LIMIT 1
                        """,
                    ),
                )
            )
            .mappings()
            .one()
        )

    if cast("int", row["cluster_id"]) != 1:
        raise AssertionError
    if cast("str", row["strategy_name"]) != "cluster_merge":
        raise AssertionError
    if cast("str", row["outcome"]) != "DUPLICATE":
        raise AssertionError
    if cast("str", row["reason_code"]) != "cluster_merge":
        raise AssertionError

    metadata = loads(cast("str", row["metadata_json"]))
    if metadata != {"source_cluster_ids": [2, 3], "target_cluster_id": 1}:
        raise AssertionError


@pytest.mark.asyncio
async def test_merge_is_idempotent_for_repeated_same_input(
    cluster_repository: tuple[DedupeClustersRepository, StorageRuntime],
) -> None:
    """Repeated merge input should be a no-op once sources are already merged."""
    repository, runtime = cluster_repository
    await _seed_merge_fixtures(runtime=runtime)

    first = await repository.merge_clusters(matched_cluster_ids=[1, 2, 3])
    second = await repository.merge_clusters(matched_cluster_ids=[1, 2, 3])

    if not first.recorded_event:
        raise AssertionError
    if second.moved_member_count != 0:
        raise AssertionError
    if second.removed_cluster_count != 0:
        raise AssertionError
    if second.recorded_event:
        raise AssertionError

    async with runtime.read_session_factory() as session:
        decision_count = cast(
            "int",
            (
                await session.execute(
                    text("SELECT COUNT(*) AS count FROM dedupe_decisions"),
                )
            ).scalar_one(),
        )

    if decision_count != 1:
        raise AssertionError


@pytest.mark.asyncio
async def test_merge_requires_at_least_two_unique_clusters(
    cluster_repository: tuple[DedupeClustersRepository, StorageRuntime],
) -> None:
    """Merge should reject requests with fewer than two unique cluster ids."""
    repository, _ = cluster_repository

    with pytest.raises(ValueError, match="need at least two clusters to merge"):
        _ = await repository.merge_clusters(matched_cluster_ids=[1, 1])


@pytest.mark.asyncio
async def test_merge_rejects_bool_cluster_ids(
    cluster_repository: tuple[DedupeClustersRepository, StorageRuntime],
) -> None:
    """Boolean cluster ids must not be accepted as integer identifiers."""
    repository, _ = cluster_repository

    with pytest.raises(TypeError, match="missing integer `matched_cluster_ids\\[1\\]`"):
        _ = await repository.merge_clusters(matched_cluster_ids=[1, True])


@pytest.mark.asyncio
async def test_merge_rejects_missing_target_cluster(
    cluster_repository: tuple[DedupeClustersRepository, StorageRuntime],
) -> None:
    """Merge should fail fast when the deterministic target cluster is absent."""
    repository, runtime = cluster_repository
    await _seed_merge_fixtures(runtime=runtime)

    with pytest.raises(ValueError, match="target cluster `0` does not exist"):
        _ = await repository.merge_clusters(matched_cluster_ids=[0, 2, 3])


@pytest.mark.asyncio
async def test_merge_rolls_back_when_no_event_item_is_available(
    cluster_repository: tuple[DedupeClustersRepository, StorageRuntime],
) -> None:
    """Merge must roll back if no item can be used for cluster_merge event."""
    repository, runtime = cluster_repository
    await _seed_clusters_without_merge_event_item(runtime=runtime)

    with pytest.raises(
        ValueError,
        match="cannot record merge event without target cluster item",
    ):
        _ = await repository.merge_clusters(matched_cluster_ids=[1, 2])

    async with runtime.read_session_factory() as session:
        rows = await session.execute(
            text(
                """
                SELECT id
                FROM dedupe_clusters
                ORDER BY id ASC
                """,
            ),
        )
        cluster_ids = [cast("int", row.id) for row in rows]

    if cluster_ids != [1, 2]:
        raise AssertionError


async def _insert_channel_fixtures(runtime: StorageRuntime) -> None:
    async with runtime.write_session_factory() as session:
        _ = await session.execute(
            text(
                """
                INSERT INTO telegram_accounts (id, api_id, api_hash_encrypted)
                VALUES (1, 111, X'01')
                """,
            ),
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
                VALUES (11, 1, 1111, 'chan-a', 'chan_a', 1)
                """,
            ),
        )
        await session.commit()


async def _seed_merge_fixtures(*, runtime: StorageRuntime) -> None:
    for item_id in (101, 102, 103, 104, 105):
        await _insert_item(runtime=runtime, item_id=item_id, message_id=item_id)

    async with runtime.write_session_factory() as session:
        for cluster_id, representative_item_id in ((1, 101), (2, 103), (3, 105)):
            _ = await session.execute(
                text(
                    """
                    INSERT INTO dedupe_clusters (
                        id,
                        cluster_key,
                        representative_item_id
                    )
                    VALUES (:id, :cluster_key, :representative_item_id)
                    """,
                ),
                {
                    "id": cluster_id,
                    "cluster_key": f"cluster-{cluster_id}",
                    "representative_item_id": representative_item_id,
                },
            )

        for cluster_id, item_id in ((1, 101), (1, 102), (2, 103), (2, 104), (3, 105)):
            _ = await session.execute(
                text(
                    """
                    INSERT INTO dedupe_members (cluster_id, item_id)
                    VALUES (:cluster_id, :item_id)
                    """,
                ),
                {"cluster_id": cluster_id, "item_id": item_id},
            )

        await session.commit()


async def _seed_clusters_without_merge_event_item(*, runtime: StorageRuntime) -> None:
    async with runtime.write_session_factory() as session:
        for cluster_id in (1, 2):
            _ = await session.execute(
                text(
                    """
                    INSERT INTO dedupe_clusters (
                        id,
                        cluster_key,
                        representative_item_id
                    )
                    VALUES (:id, :cluster_key, NULL)
                    """,
                ),
                {"id": cluster_id, "cluster_key": f"cluster-empty-{cluster_id}"},
            )

        await session.commit()


async def _insert_item(
    *,
    runtime: StorageRuntime,
    item_id: int,
    message_id: int,
) -> None:
    async with runtime.write_session_factory() as session:
        _ = await session.execute(
            text(
                """
                INSERT INTO items (
                    id,
                    channel_id,
                    message_id,
                    title,
                    body,
                    canonical_url,
                    canonical_url_hash,
                    content_hash,
                    dedupe_state
                )
                VALUES (
                    :id,
                    :channel_id,
                    :message_id,
                    :title,
                    :body,
                    NULL,
                    NULL,
                    NULL,
                    'pending'
                )
                """,
            ),
            {
                "id": item_id,
                "channel_id": 11,
                "message_id": message_id,
                "title": f"title-{item_id}",
                "body": f"body-{item_id}",
            },
        )
        await session.commit()
