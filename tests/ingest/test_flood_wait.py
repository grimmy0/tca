"""Tests for Telegram flood wait handling using mocks."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from telethon.errors import FloodWaitError  # pyright: ignore[reportMissingTypeStubs]

from tca.ingest import fetch_recent_messages

if TYPE_CHECKING:
    from tests.mocks.mock_telegram_client import MockTelegramClient


@pytest.mark.asyncio
async def test_flood_wait_mock(mock_tg_client: MockTelegramClient) -> None:
    """Verify flood wait from message fetching surfaces through ingest service."""
    wait_seconds = 300
    exc = FloodWaitError(None)
    exc.seconds = wait_seconds
    mock_tg_client.responses["iter_messages"] = exc

    with pytest.raises(FloodWaitError) as excinfo:
        _ = await fetch_recent_messages(mock_tg_client, "channel://news", limit=5)

    if excinfo.value.seconds != wait_seconds:
        raise AssertionError
    if mock_tg_client.call_counts.get("iter_messages") != 1:
        raise AssertionError


@pytest.mark.asyncio
async def test_message_fetch_mock_returns_scripted_messages(
    mock_tg_client: MockTelegramClient,
) -> None:
    """Verify message fetch path returns deterministic scripted payload."""
    scripted_messages = [{"id": 1, "text": "a"}, {"id": 2, "text": "b"}]
    mock_tg_client.responses["iter_messages"] = scripted_messages

    result = await fetch_recent_messages(mock_tg_client, "channel://news", limit=2)

    if result != scripted_messages:
        raise AssertionError
    if mock_tg_client.call_counts.get("iter_messages") != 1:
        raise AssertionError
