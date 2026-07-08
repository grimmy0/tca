"""Candidate reduction stage for dedupe matching using SQL queries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

MAX_CANDIDATES_DEFAULT = 50


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    """Minimal item fields required for candidate reduction."""

    item_id: int
    published_at: datetime | None
    canonical_url_hash: str | None
    url_domain: str | None
    rare_title_tokens: frozenset[str]


async def select_candidates(
    session: AsyncSession,
    *,
    new_item: CandidateRecord,
    horizon: timedelta,
    max_candidates: int = MAX_CANDIDATES_DEFAULT,
) -> list[CandidateRecord]:
    """Filter and cap candidate records using horizon and blocking keys via SQL."""
    if max_candidates <= 0:
        return []

    conditions = ["id != :new_item_id"]
    new_pub_iso = (
        new_item.published_at.isoformat() if new_item.published_at else None
    )
    params: dict[str, Any] = {
        "new_item_id": new_item.item_id,
        "published_at": new_pub_iso,
        "horizon_seconds": int(horizon.total_seconds()),
        "canonical_url_hash": new_item.canonical_url_hash,
        "url_domain": new_item.url_domain,
        "max_candidates": max_candidates,
    }

    # Time horizon filter: candidate published_at must be within horizon.
    # If either is NULL, it matches (is considered within horizon).
    time_filter = (
        "(published_at IS NULL OR :published_at IS NULL "
        "OR ABS(strftime('%s', published_at) - "
        "strftime('%s', :published_at)) <= :horizon_seconds)"
    )
    conditions.append(time_filter)

    # Blocking keys conditions: must match at least one blocking key.
    blocking_conditions = []
    if new_item.canonical_url_hash:
        blocking_conditions.append("canonical_url_hash = :canonical_url_hash")
    if new_item.url_domain:
        # Match domain in canonical_url (e.g. https://domain/... or https://domain)
        blocking_conditions.append(
            "(canonical_url LIKE '%//' || :url_domain || '/%' "
            "OR canonical_url LIKE '%//' || :url_domain)"
        )

    for i, token in enumerate(new_item.rare_title_tokens):
        param_name = f"token_{i}"
        params[param_name] = token
        # Check if the title contains the token case-insensitively
        blocking_conditions.append(f"title LIKE '%' || :{param_name} || '%'")

    if not blocking_conditions:
        return []

    conditions.append(f"({' OR '.join(blocking_conditions)})")

    # Safe dynamic construction from whitelist operators/parameters.
    query_str = f"""
        SELECT id, published_at, canonical_url, canonical_url_hash, title
        FROM items
        WHERE {' AND '.join(conditions)}
        ORDER BY id ASC
        LIMIT :max_candidates
    """  # noqa: S608

    statement = text(query_str)
    result = await session.execute(statement, params)
    rows = result.mappings().all()

    candidates = []
    for row in rows:
        item_id = int(row["id"])
        published_at = None
        if row["published_at"]:
            from datetime import UTC
            published_at = datetime.fromisoformat(row["published_at"])
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=UTC)

        canonical_url = row["canonical_url"]
        url_domain = None
        if canonical_url:
            from urllib.parse import urlsplit
            url_domain = urlsplit(canonical_url).netloc
            if not url_domain:
                url_domain = None

        # Re-tokenize title to construct rare_title_tokens matching new_item
        title = row["title"]
        rare_tokens = frozenset()
        if title:
            # Reconstruct tokens
            tokens = frozenset(title.split())
            rare_tokens = frozenset(
                t for t in new_item.rare_title_tokens if t in tokens
            )

        candidates.append(
            CandidateRecord(
                item_id=item_id,
                published_at=published_at,
                canonical_url_hash=row["canonical_url_hash"],
                url_domain=url_domain,
                rare_title_tokens=rare_tokens,
            )
        )

    return candidates
