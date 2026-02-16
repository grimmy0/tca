"""Tests for Telegram auth start using mocks."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests.mocks.mock_telegram_client import MockTelegramClient


@pytest.mark.asyncio
async def test_auth_start_mock(mock_tg_client: MockTelegramClient) -> None:
    """Verify that we can use the mock for auth start flow."""
    phone = "+1234567890"
    result = await mock_tg_client.send_code_request(phone)
    assert result is True  # noqa: S101
    assert mock_tg_client.is_connected() is False  # noqa: S101

    await mock_tg_client.start()
    assert mock_tg_client.is_connected() is True  # noqa: S101
