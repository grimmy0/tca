"""Candidate reduction stage for dedupe matching."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime, timedelta

MAX_CANDIDATES_DEFAULT = 50


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    """Minimal item fields required for candidate reduction."""

    item_id: int
    published_at: datetime | None
    canonical_url_hash: str | None
    url_domain: str | None
    rare_title_tokens: frozenset[str]


def select_candidates(
    *,
    new_item: CandidateRecord,
    existing_items: Iterable[CandidateRecord],
    horizon: timedelta,
    max_candidates: int = MAX_CANDIDATES_DEFAULT,
) -> list[CandidateRecord]:
    """Filter and cap candidate records using horizon and blocking keys."""
    if max_candidates <= 0:
        return []

    reduced = [
        candidate
        for candidate in existing_items
        if candidate.item_id != new_item.item_id
        and _within_horizon(new_item=new_item, candidate=candidate, horizon=horizon)
        and _blocking_key_matches(new_item=new_item, candidate=candidate)
    ]
    reduced.sort(key=lambda candidate: candidate.item_id)
    return reduced[:max_candidates]


def _within_horizon(
    *,
    new_item: CandidateRecord,
    candidate: CandidateRecord,
    horizon: timedelta,
) -> bool:
    if new_item.published_at is None or candidate.published_at is None:
        return True
    return abs(new_item.published_at - candidate.published_at) <= horizon


def _blocking_key_matches(
    *,
    new_item: CandidateRecord,
    candidate: CandidateRecord,
) -> bool:
    if (
        new_item.canonical_url_hash is not None
        and candidate.canonical_url_hash is not None
        and new_item.canonical_url_hash == candidate.canonical_url_hash
    ):
        return True

    if (
        new_item.url_domain is not None
        and candidate.url_domain is not None
        and new_item.url_domain == candidate.url_domain
    ):
        return True

    return bool(new_item.rare_title_tokens & candidate.rare_title_tokens)
