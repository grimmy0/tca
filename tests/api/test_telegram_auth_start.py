"""Tests for Telegram auth start using mocks."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tca.auth import request_login_code

if TYPE_CHECKING:
    from tests.mocks.mock_telegram_client import MockTelegramClient


@pytest.mark.asyncio
async def test_auth_start_mock(mock_tg_client: MockTelegramClient) -> None:
    """Verify auth flow uses injected client and supports falsy scripted responses."""
    phone = "+1234567890"
    mock_tg_client.responses["send_code_request"] = False

    result = await request_login_code(mock_tg_client, phone)

    if result is not False:
        raise AssertionError
    if mock_tg_client.call_counts.get("send_code_request") != 1:
        raise AssertionError
