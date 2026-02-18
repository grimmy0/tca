"""Repository helpers for item upsert storage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from sqlalchemy import text

if TYPE_CHECKING:
    from tca.storage.db import SessionFactory


@dataclass(slots=True, frozen=True)
class ItemRecord:
    """Typed items row payload returned from repository upserts."""

    item_id: int
    channel_id: int
    message_id: int
    raw_message_id: int | None
    published_at: datetime | None
    title: str | None
    body: str | None
    canonical_url: str | None
    canonical_url_hash: str | None
    content_hash: str | None
    dedupe_state: str
    created_at: datetime
    updated_at: datetime


class ItemsRepositoryError(RuntimeError):
    """Base exception for item repository operations."""


class ItemsRepository:
    """Upsert helper for normalized items."""

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

    async def upsert_item(  # noqa: PLR0913
        self,
        *,
        channel_id: int,
        message_id: int,
        raw_message_id: int | None,
        published_at: datetime | None,
        title: str | None,
        body: str | None,
        canonical_url: str | None,
        canonical_url_hash: str | None,
        content_hash: str | None,
    ) -> ItemRecord:
        """Insert or update a normalized item for a channel message id."""
        statement = text(
            """
            INSERT INTO items (
                channel_id,
                message_id,
                raw_message_id,
                published_at,
                title,
                body,
                canonical_url,
                canonical_url_hash,
                content_hash
            )
            VALUES (
                :channel_id,
                :message_id,
                :raw_message_id,
                :published_at,
                :title,
                :body,
                :canonical_url,
                :canonical_url_hash,
                :content_hash
            )
            ON CONFLICT(channel_id, message_id)
            DO UPDATE SET
                raw_message_id = COALESCE(
                    items.raw_message_id,
                    excluded.raw_message_id
                ),
                published_at = excluded.published_at,
                title = excluded.title,
                body = excluded.body,
                canonical_url = excluded.canonical_url,
                canonical_url_hash = excluded.canonical_url_hash,
                content_hash = excluded.content_hash,
                updated_at = CURRENT_TIMESTAMP
            RETURNING
                id,
                channel_id,
                message_id,
                raw_message_id,
                published_at,
                title,
                body,
                canonical_url,
                canonical_url_hash,
                content_hash,
                dedupe_state,
                created_at,
                updated_at
            """,
        )
        async with self._write_session_factory() as session:
            result = await session.execute(
                statement,
                {
                    "channel_id": channel_id,
                    "message_id": message_id,
                    "raw_message_id": raw_message_id,
                    "published_at": published_at,
                    "title": title,
                    "body": body,
                    "canonical_url": canonical_url,
                    "canonical_url_hash": canonical_url_hash,
                    "content_hash": content_hash,
                },
            )
            row = result.mappings().one()
            await session.commit()
        return _decode_item_row(row)


def _decode_item_row(row: object) -> ItemRecord:
    row_map = cast("dict[str, object]", row)
    return ItemRecord(
        item_id=_coerce_int(value=row_map.get("id"), field="id"),
        channel_id=_coerce_int(value=row_map.get("channel_id"), field="channel_id"),
        message_id=_coerce_int(value=row_map.get("message_id"), field="message_id"),
        raw_message_id=_coerce_optional_int(
            value=row_map.get("raw_message_id"),
            field="raw_message_id",
        ),
        published_at=_coerce_optional_datetime(
            value=row_map.get("published_at"),
            field="published_at",
        ),
        title=_coerce_optional_str(value=row_map.get("title")),
        body=_coerce_optional_str(value=row_map.get("body")),
        canonical_url=_coerce_optional_str(value=row_map.get("canonical_url")),
        canonical_url_hash=_coerce_optional_str(
            value=row_map.get("canonical_url_hash"),
        ),
        content_hash=_coerce_optional_str(value=row_map.get("content_hash")),
        dedupe_state=_coerce_str(
            value=row_map.get("dedupe_state"),
            field="dedupe_state",
        ),
        created_at=_coerce_datetime(
            value=row_map.get("created_at"),
            field="created_at",
        ),
        updated_at=_coerce_datetime(
            value=row_map.get("updated_at"),
            field="updated_at",
        ),
    )


def _coerce_int(*, value: object, field: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    msg = f"missing integer `{field}`"
    raise ItemsRepositoryError(msg)


def _coerce_optional_int(*, value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    msg = f"invalid `{field}` value"
    raise ItemsRepositoryError(msg)


def _coerce_str(*, value: object, field: str) -> str:
    if isinstance(value, str):
        return value
    msg = f"missing `{field}`"
    raise ItemsRepositoryError(msg)


def _coerce_optional_str(*, value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    msg = "invalid string value"
    raise ItemsRepositoryError(msg)


def _coerce_datetime(*, value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return _parse_datetime(value, field=field)
    msg = f"missing `{field}`"
    raise ItemsRepositoryError(msg)


def _coerce_optional_datetime(*, value: object, field: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return _parse_datetime(value, field=field)
    msg = f"invalid `{field}` value"
    raise ItemsRepositoryError(msg)


def _parse_datetime(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        msg = f"invalid `{field}` value"
        raise ItemsRepositoryError(msg) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
