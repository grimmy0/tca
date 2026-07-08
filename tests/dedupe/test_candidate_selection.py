"""Unit tests for candidate reduction stage behavior using database queries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

from tca.config.settings import load_settings
from tca.dedupe import CandidateRecord, select_candidates
from tca.storage import (
    create_storage_runtime,
    dispose_storage_runtime,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest.fixture
async def db_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncSession]:
    """Build temporary SQLite session factory for candidate selection tests."""
    db_path = tmp_path / "candidate-selection-test.sqlite3"
    monkeypatch.setenv("TCA_DB_PATH", db_path.as_posix())
    settings = load_settings()
    runtime = create_storage_runtime(settings)

    async with runtime.write_engine.begin() as connection:
        await connection.exec_driver_sql("PRAGMA foreign_keys=OFF;")
        await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY,
                channel_id INTEGER NOT NULL DEFAULT 1,
                message_id BIGINT NOT NULL DEFAULT 1,
                published_at DATETIME NULL,
                title TEXT NULL,
                body TEXT NULL,
                canonical_url TEXT NULL,
                canonical_url_hash VARCHAR(64) NULL,
                content_hash VARCHAR(64) NULL
            )
            """
        )

    async with runtime.write_session_factory() as session:
        yield session

    await dispose_storage_runtime(runtime)


async def _seed_candidates(
    session: AsyncSession,
    candidates: list[CandidateRecord],
) -> None:
    """Helper to seed candidate records into the database items table."""
    statement = text(
        """
        INSERT INTO items (id, published_at, canonical_url, canonical_url_hash, title)
        VALUES (:id, :published_at, :canonical_url, :canonical_url_hash, :title)
        """
    )
    for c in candidates:
        canonical_url = f"https://{c.url_domain}/path" if c.url_domain else None
        title = " ".join(c.rare_title_tokens) if c.rare_title_tokens else None

        await session.execute(
            statement,
            {
                "id": c.item_id,
                "published_at": (
                    c.published_at.isoformat() if c.published_at else None
                ),
                "canonical_url": canonical_url,
                "canonical_url_hash": c.canonical_url_hash,
                "title": title,
            },
        )
    await session.commit()


@pytest.mark.asyncio
async def test_candidates_outside_horizon_are_excluded(
    db_session: AsyncSession,
) -> None:
    """Candidates older/newer than configured horizon should be excluded."""
    base_time = datetime.now(UTC)
    new_item = _candidate(
        item_id=100,
        published_at=base_time,
        canonical_url_hash="hash-a",
        url_domain="example.com",
        rare_title_tokens=frozenset({"alpha"}),
    )
    inside_horizon = _candidate(
        item_id=200,
        published_at=base_time - timedelta(hours=1),
        canonical_url_hash="hash-a",
        url_domain="other.example",
        rare_title_tokens=frozenset(),
    )
    outside_horizon = _candidate(
        item_id=300,
        published_at=base_time - timedelta(days=3),
        canonical_url_hash="hash-a",
        url_domain="other.example",
        rare_title_tokens=frozenset(),
    )

    await _seed_candidates(db_session, [outside_horizon, inside_horizon])

    selected = await select_candidates(
        db_session,
        new_item=new_item,
        horizon=timedelta(hours=24),
    )

    if [candidate.item_id for candidate in selected] != [200]:
        raise AssertionError


@pytest.mark.asyncio
async def test_blocking_keys_reduce_candidate_set_deterministically(
    db_session: AsyncSession,
) -> None:
    """Only blocking-key matches are kept, and output order is deterministic."""
    base_time = datetime.now(UTC)
    new_item = _candidate(
        item_id=100,
        published_at=base_time,
        canonical_url_hash="hash-a",
        url_domain="news.example",
        rare_title_tokens=frozenset({"rare-one", "rare-two"}),
    )
    by_hash = _candidate(
        item_id=40,
        published_at=base_time - timedelta(minutes=5),
        canonical_url_hash="hash-a",
        url_domain="other.example",
        rare_title_tokens=frozenset(),
    )
    by_domain = _candidate(
        item_id=10,
        published_at=base_time - timedelta(minutes=10),
        canonical_url_hash="hash-b",
        url_domain="news.example",
        rare_title_tokens=frozenset(),
    )
    by_rare_token = _candidate(
        item_id=30,
        published_at=base_time - timedelta(minutes=15),
        canonical_url_hash="hash-c",
        url_domain="other.example",
        rare_title_tokens=frozenset({"rare-two"}),
    )
    non_match = _candidate(
        item_id=20,
        published_at=base_time - timedelta(minutes=20),
        canonical_url_hash="hash-d",
        url_domain="elsewhere.example",
        rare_title_tokens=frozenset({"not-shared"}),
    )

    await _seed_candidates(
        db_session, [by_hash, non_match, by_rare_token, by_domain]
    )

    selected = await select_candidates(
        db_session,
        new_item=new_item,
        horizon=timedelta(hours=24),
    )

    if [candidate.item_id for candidate in selected] != [10, 30, 40]:
        raise AssertionError


@pytest.mark.asyncio
async def test_candidate_count_never_exceeds_cap(
    db_session: AsyncSession,
) -> None:
    """Selection should always enforce the configured candidate cap."""
    base_time = datetime.now(UTC)
    cap = 50
    new_item = _candidate(
        item_id=100,
        published_at=base_time,
        canonical_url_hash="hash-a",
        url_domain="example.com",
        rare_title_tokens=frozenset({"alpha"}),
    )
    candidates = [
        _candidate(
            item_id=item_id,
            published_at=base_time - timedelta(hours=1),
            canonical_url_hash="hash-a",
            url_domain="other.example",
            rare_title_tokens=frozenset(),
        )
        for item_id in range(1, 101)
    ]

    await _seed_candidates(db_session, candidates)

    selected = await select_candidates(
        db_session,
        new_item=new_item,
        horizon=timedelta(hours=24),
        max_candidates=cap,
    )

    if len(selected) != cap:
        raise AssertionError


def _candidate(
    *,
    item_id: int,
    published_at: datetime,
    canonical_url_hash: str | None,
    url_domain: str | None,
    rare_title_tokens: frozenset[str],
) -> CandidateRecord:
    return CandidateRecord(
        item_id=item_id,
        published_at=published_at,
        canonical_url_hash=canonical_url_hash,
        url_domain=url_domain,
        rare_title_tokens=rare_title_tokens,
    )
