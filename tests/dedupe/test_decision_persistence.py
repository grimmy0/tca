"""Tests for dedupe decision explainability persistence and retrieval."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from tca.config.settings import load_settings
from tca.dedupe import StrategyDecisionAttempt, execute_strategy_chain_with_trace
from tca.storage import (
    DedupeDecisionsRepository,
    StorageRuntime,
    create_storage_runtime,
    dispose_storage_runtime,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

MATCH_SCORE = 0.98
EXPECTED_ATTEMPTS_PER_TRACE = 2
PRIMARY_ITEM_ID = 101
PRIMARY_CLUSTER_ID = 1


@pytest.fixture
async def decisions_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[DedupeDecisionsRepository, StorageRuntime]]:
    """Build repository and schema for decision persistence tests."""
    db_path = tmp_path / "decision-persistence.sqlite3"
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

    await _seed_fixtures(runtime=runtime)

    try:
        yield (
            DedupeDecisionsRepository(
                read_session_factory=runtime.read_session_factory,
                write_session_factory=runtime.write_session_factory,
            ),
            runtime,
        )
    finally:
        await dispose_storage_runtime(runtime)


@pytest.mark.asyncio
async def test_every_dedupe_attempt_persists_decision_records(
    decisions_repository: tuple[DedupeDecisionsRepository, StorageRuntime],
) -> None:
    """Every evaluated strategy attempt should create one decision row."""
    repository, _ = decisions_repository
    attempts = _build_attempts()

    inserted_ids = await repository.persist_attempts(
        item_id=PRIMARY_ITEM_ID,
        cluster_id=PRIMARY_CLUSTER_ID,
        candidate_item_id=102,
        decision_attempts=attempts,
    )

    if len(inserted_ids) != len(attempts):
        raise AssertionError


@pytest.mark.asyncio
async def test_record_includes_strategy_outcome_reason_and_score_when_present(
    decisions_repository: tuple[DedupeDecisionsRepository, StorageRuntime],
) -> None:
    """Persisted decision rows should include key explainability fields."""
    repository, _ = decisions_repository

    _ = await repository.persist_attempts(
        item_id=PRIMARY_ITEM_ID,
        cluster_id=PRIMARY_CLUSTER_ID,
        candidate_item_id=102,
        decision_attempts=_build_attempts(),
    )
    rows = await repository.list_for_item(item_id=PRIMARY_ITEM_ID)

    if [row.strategy_name for row in rows] != ["exact_url", "title_similarity"]:
        raise AssertionError
    if [row.outcome for row in rows] != ["ABSTAIN", "DUPLICATE"]:
        raise AssertionError
    if [row.reason_code for row in rows] != [
        "exact_url_missing",
        "title_similarity_match",
    ]:
        raise AssertionError
    if rows[0].score is not None:
        raise AssertionError
    if rows[1].score != MATCH_SCORE:
        raise AssertionError


@pytest.mark.asyncio
async def test_decisions_can_be_retrieved_by_item_or_cluster(
    decisions_repository: tuple[DedupeDecisionsRepository, StorageRuntime],
) -> None:
    """Item and cluster scoped read paths should return expected decision rows."""
    repository, _ = decisions_repository

    _ = await repository.persist_attempts(
        item_id=PRIMARY_ITEM_ID,
        cluster_id=PRIMARY_CLUSTER_ID,
        candidate_item_id=102,
        decision_attempts=_build_attempts(),
    )
    _ = await repository.persist_attempts(
        item_id=103,
        cluster_id=2,
        candidate_item_id=104,
        decision_attempts=(
            StrategyDecisionAttempt(
                strategy_name="content_hash",
                outcome="DISTINCT",
                reason="content_hash_mismatch",
                score=None,
            ),
        ),
    )

    by_item = await repository.list_for_item(item_id=PRIMARY_ITEM_ID)
    by_cluster = await repository.list_for_cluster(cluster_id=PRIMARY_CLUSTER_ID)

    if len(by_item) != EXPECTED_ATTEMPTS_PER_TRACE:
        raise AssertionError
    if len(by_cluster) != EXPECTED_ATTEMPTS_PER_TRACE:
        raise AssertionError
    if any(row.item_id != PRIMARY_ITEM_ID for row in by_item):
        raise AssertionError
    if any(row.cluster_id != PRIMARY_CLUSTER_ID for row in by_cluster):
        raise AssertionError


def _build_attempts() -> tuple[StrategyDecisionAttempt, ...]:
    result, attempts = execute_strategy_chain_with_trace(
        strategies=(
            (
                "exact_url",
                lambda: {"status": "ABSTAIN", "reason": "exact_url_missing"},
            ),
            (
                "title_similarity",
                lambda: {
                    "status": "DUPLICATE",
                    "reason": "title_similarity_match",
                    "score": MATCH_SCORE,
                },
            ),
        ),
    )
    if result["status"] != "DUPLICATE":
        raise AssertionError
    return attempts


async def _seed_fixtures(*, runtime: StorageRuntime) -> None:
    async with runtime.write_session_factory() as session:
        _ = await session.execute(
            text(
                """
                INSERT INTO telegram_accounts (id, api_id, api_hash_encrypted)
                VALUES (1, 1001, X'ABCD')
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
                    name
                )
                VALUES (1, 1, 5001, 'decision-test-channel')
                """,
            ),
        )
        _ = await session.execute(
            text(
                """
                INSERT INTO items (id, channel_id, message_id)
                VALUES
                    (101, 1, 9001),
                    (102, 1, 9002),
                    (103, 1, 9003),
                    (104, 1, 9004)
                """,
            ),
        )
        _ = await session.execute(
            text(
                """
                INSERT INTO dedupe_clusters (id, cluster_key, representative_item_id)
                VALUES
                    (1, 'cluster-one', 101),
                    (2, 'cluster-two', 103)
                """,
            ),
        )
        await session.commit()
