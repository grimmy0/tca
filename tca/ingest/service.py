"""Ingestion service helpers for Telegram message retrieval."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

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


async def fetch_recent_messages(
    client: MessageFetchClient,
    channel: object,
    *,
    limit: int | None = None,
) -> list[object]:
    """Collect messages from the client iterator into a list."""
    return [message async for message in client.iter_messages(channel, limit=limit)]


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
