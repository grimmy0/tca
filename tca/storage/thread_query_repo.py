"""Repository helpers for on-demand deduplicated thread timeline queries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from sqlalchemy import text

if TYPE_CHECKING:
    from tca.storage.db import SessionFactory


@dataclass(slots=True, frozen=True)
class ThreadEntryRecord:
    """One timeline row representing a dedupe cluster representative item."""

    cluster_id: int
    cluster_key: str
    representative_item_id: int
    representative_published_at: datetime | None
    representative_title: str | None
    representative_body: str | None
    representative_canonical_url: str | None
    representative_channel_id: int
    representative_channel_name: str
    representative_channel_username: str | None
    duplicate_count: int


class ThreadQueryRepositoryError(RuntimeError):
    """Base exception for thread query repository operations."""


class ThreadQueryRepository:
    """Read-only repository for paginated thread timeline query rows."""

    _read_session_factory: SessionFactory
    _write_session_factory: SessionFactory

    def __init__(
        self,
        *,
        read_session_factory: SessionFactory,
        write_session_factory: SessionFactory,
    ) -> None:
        """Create repository with explicit read/write session dependencies."""
        self._read_session_factory = read_session_factory
        self._write_session_factory = write_session_factory

    async def list_entries(
        self,
        *,
        page: int,
        page_size: int,
    ) -> list[ThreadEntryRecord]:
        """Return one deterministic page of representative thread entries."""
        page_number = _coerce_positive_int(value=page, field="page")
        size = _coerce_positive_int(value=page_size, field="page_size")
        offset = (page_number - 1) * size

        statement = text(
            """
            SELECT
                clusters.id AS cluster_id,
                clusters.cluster_key AS cluster_key,
                representative.id AS representative_item_id,
                representative.published_at AS representative_published_at,
                representative.title AS representative_title,
                representative.body AS representative_body,
                representative.canonical_url AS representative_canonical_url,
                representative_channel.id AS representative_channel_id,
                representative_channel.name AS representative_channel_name,
                representative_channel.username AS representative_channel_username,
                COUNT(members.item_id) AS duplicate_count
            FROM dedupe_clusters AS clusters
            INNER JOIN items AS representative
                ON representative.id = clusters.representative_item_id
            INNER JOIN telegram_channels AS representative_channel
                ON representative_channel.id = representative.channel_id
            LEFT JOIN dedupe_members AS members
                ON members.cluster_id = clusters.id
            LEFT JOIN items AS member_items
                ON member_items.id = members.item_id
            LEFT JOIN telegram_channels AS member_channels
                ON member_channels.id = member_items.channel_id
            GROUP BY
                clusters.id,
                clusters.cluster_key,
                representative.id,
                representative.published_at,
                representative.title,
                representative.body,
                representative.canonical_url,
                representative_channel.id,
                representative_channel.name,
                representative_channel.username
            ORDER BY
                CASE
                    WHEN representative.published_at IS NULL THEN 1
                    ELSE 0
                END ASC,
                representative.published_at DESC,
                clusters.id DESC
            LIMIT :limit
            OFFSET :offset
            """,
        )
        async with self._read_session_factory() as session:
            result = await session.execute(
                statement,
                {
                    "limit": size,
                    "offset": offset,
                },
            )
            rows = result.mappings().all()
        return [_decode_thread_entry_row(row) for row in rows]


def _decode_thread_entry_row(row: object) -> ThreadEntryRecord:
    row_map = cast("dict[str, object]", row)
    return ThreadEntryRecord(
        cluster_id=_coerce_positive_int(
            value=row_map.get("cluster_id"),
            field="cluster_id",
        ),
        cluster_key=_coerce_str(value=row_map.get("cluster_key"), field="cluster_key"),
        representative_item_id=_coerce_positive_int(
            value=row_map.get("representative_item_id"),
            field="representative_item_id",
        ),
        representative_published_at=_coerce_optional_datetime(
            value=row_map.get("representative_published_at"),
            field="representative_published_at",
        ),
        representative_title=_coerce_optional_str(
            value=row_map.get("representative_title"),
        ),
        representative_body=_coerce_optional_str(
            value=row_map.get("representative_body"),
        ),
        representative_canonical_url=_coerce_optional_str(
            value=row_map.get("representative_canonical_url"),
        ),
        representative_channel_id=_coerce_positive_int(
            value=row_map.get("representative_channel_id"),
            field="representative_channel_id",
        ),
        representative_channel_name=_coerce_str(
            value=row_map.get("representative_channel_name"),
            field="representative_channel_name",
        ),
        representative_channel_username=_coerce_optional_str(
            value=row_map.get("representative_channel_username"),
        ),
        duplicate_count=_coerce_non_negative_int(
            value=row_map.get("duplicate_count"),
            field="duplicate_count",
        ),
    )


def _coerce_positive_int(*, value: object, field: str) -> int:
    if isinstance(value, bool):
        msg = f"invalid `{field}` value"
        raise ThreadQueryRepositoryError(msg)
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        if parsed > 0:
            return parsed
    msg = f"invalid `{field}` value"
    raise ThreadQueryRepositoryError(msg)


def _coerce_non_negative_int(*, value: object, field: str) -> int:
    if isinstance(value, bool):
        msg = f"invalid `{field}` value"
        raise ThreadQueryRepositoryError(msg)
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    msg = f"invalid `{field}` value"
    raise ThreadQueryRepositoryError(msg)


def _coerce_str(*, value: object, field: str) -> str:
    if isinstance(value, str):
        return value
    msg = f"invalid `{field}` value"
    raise ThreadQueryRepositoryError(msg)


def _coerce_optional_str(*, value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    message = "invalid string value"
    raise ThreadQueryRepositoryError(message)


def _coerce_optional_datetime(*, value: object, field: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = _parse_datetime(value=value, field=field)
    else:
        msg = f"invalid `{field}` value"
        raise ThreadQueryRepositoryError(msg)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed


def _parse_datetime(*, value: str, field: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        msg = f"invalid `{field}` value"
        raise ThreadQueryRepositoryError(msg) from exc
