"""Tests for Telegram flood wait handling using mocks."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from telethon.errors import FloodWaitError  # pyright: ignore[reportMissingTypeStubs]

if TYPE_CHECKING:
    from tests.mocks.mock_telegram_client import MockTelegramClient


@pytest.mark.asyncio
async def test_flood_wait_mock(mock_tg_client: MockTelegramClient) -> None:
    """Verify that we can script exceptions in the mock."""
    wait_seconds = 300
    exc = FloodWaitError(None)
    exc.seconds = wait_seconds
    mock_tg_client.responses["send_code_request"] = exc

    with pytest.raises(FloodWaitError) as excinfo:
        _ = await mock_tg_client.send_code_request("+1234567890")

    assert excinfo.value.seconds == wait_seconds  # noqa: S101
