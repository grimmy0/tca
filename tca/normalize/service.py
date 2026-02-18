"""Normalization service helpers for item upserts."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from tca.storage import WriterQueueProtocol


class ItemWriteRepository(Protocol):
    """Write contract for normalized item upsert operations."""

    async def upsert_item(
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
    ) -> object:
        """Insert or update one normalized item."""
        ...


async def upsert_item(
    writer_queue: WriterQueueProtocol,
    repository: ItemWriteRepository,
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
) -> object:
    """Persist one normalized item through writer-queue serialization."""

    async def _write() -> object:
        return await repository.upsert_item(
            channel_id=channel_id,
            message_id=message_id,
            raw_message_id=raw_message_id,
            published_at=published_at,
            title=title,
            body=body,
            canonical_url=canonical_url,
            canonical_url_hash=canonical_url_hash,
            content_hash=content_hash,
        )

    return await writer_queue.submit(_write)
