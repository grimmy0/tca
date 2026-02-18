"""Tests for bounded pagination logic in ingest polling."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from tca.ingest import fetch_bounded_messages
from tca.storage import ChannelCursor


@dataclass(slots=True, frozen=True)
class FakeMessage:
    """Simple message stub with an integer id field."""

    id: int


@dataclass(slots=True)
class ScriptedPageClient:
    """Client stub that returns scripted pages in sequence."""

    pages: list[list[FakeMessage]]
    call_offsets: list[int | None] = field(default_factory=list)
    call_limits: list[int] = field(default_factory=list)

    async def get_messages(
        self,
        entity: object,
        *,
        limit: int,
        offset_id: int | None = None,
    ) -> list[FakeMessage]:
        """Return next scripted page and record request parameters."""
        _ = entity
        self.call_offsets.append(offset_id)
        self.call_limits.append(limit)
        if not self.pages:
            return []
        page = self.pages.pop(0)
        return page[:limit]


def _fixed_time() -> datetime:
    return datetime(2025, 2, 16, 12, 0, tzinfo=UTC) + timedelta(0)


@pytest.mark.asyncio
async def test_pagination_stops_on_page_limit() -> None:
    """Ensure polling stops once max pages is reached."""
    client = ScriptedPageClient(
        pages=[
            [FakeMessage(30), FakeMessage(29), FakeMessage(28)],
            [FakeMessage(27), FakeMessage(26), FakeMessage(25)],
            [FakeMessage(24), FakeMessage(23), FakeMessage(22)],
        ],
    )

    result = await fetch_bounded_messages(
        client,
        channel=object(),
        cursor=None,
        max_pages_per_poll=2,
        max_messages_per_poll=10,
        page_size=3,
        time_provider=_fixed_time,
    )

    if len(result.messages) != 6:  # noqa: PLR2004
        raise AssertionError
    if result.cursor.next_offset_id != 25:  # noqa: PLR2004
        raise AssertionError
    if client.call_offsets != [None, 28]:
        raise AssertionError


@pytest.mark.asyncio
async def test_pagination_stops_on_message_limit_and_sets_offset() -> None:
    """Ensure pagination stops once max messages is reached."""
    client = ScriptedPageClient(
        pages=[
            [FakeMessage(10), FakeMessage(9), FakeMessage(8)],
            [FakeMessage(7), FakeMessage(6)],
            [FakeMessage(5), FakeMessage(4)],
        ],
    )

    result = await fetch_bounded_messages(
        client,
        channel=object(),
        cursor=None,
        max_pages_per_poll=5,
        max_messages_per_poll=5,
        page_size=3,
        time_provider=_fixed_time,
    )

    if len(result.messages) != 5:  # noqa: PLR2004
        raise AssertionError
    if result.cursor.next_offset_id != 6:  # noqa: PLR2004
        raise AssertionError
    if client.call_offsets != [None, 8]:
        raise AssertionError


@pytest.mark.asyncio
async def test_pagination_resumes_from_stored_offset() -> None:
    """Ensure next run continues from stored cursor offset."""
    cursor = ChannelCursor(
        last_message_id=300,
        next_offset_id=200,
        last_polled_at=datetime(2025, 2, 15, 8, 0, tzinfo=UTC) + timedelta(0),
    )
    client = ScriptedPageClient(pages=[[FakeMessage(199), FakeMessage(198)]])

    result = await fetch_bounded_messages(
        client,
        channel=object(),
        cursor=cursor,
        max_pages_per_poll=3,
        max_messages_per_poll=10,
        page_size=2,
        time_provider=_fixed_time,
    )

    if not client.call_offsets or client.call_offsets[0] != 200:  # noqa: PLR2004
        raise AssertionError
    if result.cursor.next_offset_id is not None:
        raise AssertionError
    if result.cursor.last_message_id != 300:  # noqa: PLR2004
        raise AssertionError


@pytest.mark.asyncio
async def test_pagination_uses_lowest_id_for_offset() -> None:
    """Ensure offset uses the lowest id even if batch order is unexpected."""
    client = ScriptedPageClient(
        pages=[[FakeMessage(5), FakeMessage(7), FakeMessage(6)]],
    )

    result = await fetch_bounded_messages(
        client,
        channel=object(),
        cursor=None,
        max_pages_per_poll=1,
        max_messages_per_poll=10,
        page_size=3,
        time_provider=_fixed_time,
    )

    if result.cursor.next_offset_id != 5:  # noqa: PLR2004
        raise AssertionError
