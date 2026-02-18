"""Unit tests for candidate reduction stage behavior."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tca.dedupe import CandidateRecord, select_candidates


def test_candidates_outside_horizon_are_excluded() -> None:
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

    selected = select_candidates(
        new_item=new_item,
        existing_items=[outside_horizon, inside_horizon],
        horizon=timedelta(hours=24),
    )

    if [candidate.item_id for candidate in selected] != [200]:
        raise AssertionError


def test_blocking_keys_reduce_candidate_set_deterministically() -> None:
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

    selected = select_candidates(
        new_item=new_item,
        existing_items=[by_hash, non_match, by_rare_token, by_domain],
        horizon=timedelta(hours=24),
    )

    if [candidate.item_id for candidate in selected] != [10, 30, 40]:
        raise AssertionError


def test_candidate_count_never_exceeds_cap() -> None:
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

    selected = select_candidates(
        new_item=new_item,
        existing_items=candidates,
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
