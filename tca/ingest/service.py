"""Ingestion service helpers for Telegram message retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Callable, Protocol

from tca.storage import ChannelCursor

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from tca.storage import WriterQueueProtocol


class MessageFetchClient(Protocol):
    """Minimum client surface for ingest message retrieval."""

    def iter_messages(
        self,
        entity: object,
        *,
        limit: int | None = None,
    ) -> AsyncIterator[object]:
        """Yield Telegram messages for a channel or chat entity."""
        ...


class RawMessageWriteRepository(Protocol):
    """Write contract for raw message upsert operations."""

    async def upsert_raw_message(
        self,
        *,
        channel_id: int,
        message_id: int,
        payload: object,
    ) -> object:
        """Insert or update one raw message payload."""
        ...


class PagedMessageFetchClient(Protocol):
    """Client surface for bounded pagination message retrieval."""

    async def get_messages(
        self,
        entity: object,
        *,
        limit: int,
        offset_id: int | None = None,
    ) -> list[object]:
        """Return one page of messages for a channel or chat entity."""
        ...


TimeProvider = Callable[[], datetime]


@dataclass(slots=True, frozen=True)
class BoundedPaginationResult:
    """Result of a bounded pagination fetch cycle."""

    messages: list[object]
    cursor: ChannelCursor


async def fetch_recent_messages(
    client: MessageFetchClient,
    channel: object,
    *,
    limit: int | None = None,
) -> list[object]:
    """Collect messages from the client iterator into a list."""
    return [message async for message in client.iter_messages(channel, limit=limit)]


async def fetch_bounded_messages(
    client: PagedMessageFetchClient,
    channel: object,
    *,
    cursor: ChannelCursor | None,
    max_pages_per_poll: int,
    max_messages_per_poll: int,
    page_size: int = 100,
    time_provider: TimeProvider | None = None,
) -> BoundedPaginationResult:
    """Collect messages from Telegram with bounded pagination limits."""
    polled_at = _utc_now() if time_provider is None else time_provider()
    if max_pages_per_poll <= 0 or max_messages_per_poll <= 0:
        return BoundedPaginationResult(
            messages=[],
            cursor=ChannelCursor(
                last_message_id=cursor.last_message_id if cursor else None,
                next_offset_id=cursor.next_offset_id if cursor else None,
                last_polled_at=polled_at,
            ),
        )

    offset_id = cursor.next_offset_id if cursor and cursor.next_offset_id is not None else None
    most_recent_id = cursor.last_message_id if cursor else None
    messages: list[object] = []
    pages_fetched = 0
    exhausted = False

    while pages_fetched < max_pages_per_poll and len(messages) < max_messages_per_poll:
        remaining = max_messages_per_poll - len(messages)
        page_limit = min(page_size, remaining)
        batch = await client.get_messages(
            channel,
            limit=page_limit,
            offset_id=offset_id,
        )
        pages_fetched += 1
        if not batch:
            exhausted = True
            offset_id = None
            break
        messages.extend(batch)
        batch_ids = [_extract_message_id(message) for message in batch]
        batch_max = max(batch_ids)
        if most_recent_id is None or batch_max > most_recent_id:
            most_recent_id = batch_max
        offset_id = min(batch_ids)
        if len(batch) < page_limit:
            exhausted = True
            offset_id = None
            break

    next_offset_id = None if exhausted else offset_id
    return BoundedPaginationResult(
        messages=messages,
        cursor=ChannelCursor(
            last_message_id=most_recent_id,
            next_offset_id=next_offset_id,
            last_polled_at=polled_at,
        ),
    )


async def upsert_raw_message(
    writer_queue: WriterQueueProtocol,
    repository: RawMessageWriteRepository,
    *,
    channel_id: int,
    message_id: int,
    payload: object,
) -> object:
    """Persist one raw message payload through writer-queue serialization."""

    async def _write() -> object:
        return await repository.upsert_raw_message(
            channel_id=channel_id,
            message_id=message_id,
            payload=payload,
        )

    return await writer_queue.submit(_write)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _extract_message_id(message: object) -> int:
    message_id = getattr(message, "id", None)
    if isinstance(message_id, int):
        return message_id
    raise ValueError("message missing integer id")
