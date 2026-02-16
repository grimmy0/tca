"""Ingestion service helpers for Telegram message retrieval."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


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


async def fetch_recent_messages(
    client: MessageFetchClient,
    channel: object,
    *,
    limit: int | None = None,
) -> list[object]:
    """Collect messages from the client iterator into a list."""
    return [message async for message in client.iter_messages(channel, limit=limit)]
