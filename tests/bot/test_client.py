"""Tests for BotApiClient using httpx.MockTransport."""

from __future__ import annotations

import httpx
import pytest

from tca.bot import (
    BotApiClient,
    BotApiError,
    BotNetworkError,
    BotTokenInvalidError,
)


@pytest.mark.asyncio
async def test_validate_token_success() -> None:
    """Ensure validate_token returns correct BotInfo on success."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert str(request.url) == "https://api.telegram.org/bot123456:ABC/getMe"
        return httpx.Response(
            200,
            json={"ok": True, "result": {"id": 987654, "is_bot": True, "username": "my_test_bot"}},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        bot_client = BotApiClient(client=client)
        info = await bot_client.validate_token("123456:ABC")

    assert info.bot_id == 987654
    assert info.username == "my_test_bot"


@pytest.mark.asyncio
async def test_validate_token_invalid_token_401() -> None:
    """Ensure validate_token raises BotTokenInvalidError on HTTP 401."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"ok": False, "error_code": 401, "description": "Unauthorized"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        bot_client = BotApiClient(client=client)
        with pytest.raises(BotTokenInvalidError) as exc_info:
            await bot_client.validate_token("invalid-token")

    assert "401 Unauthorized" in str(exc_info.value)


@pytest.mark.asyncio
async def test_validate_token_network_error() -> None:
    """Ensure validate_token raises BotNetworkError on transport network failure."""
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection timed out", request=request)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        bot_client = BotApiClient(client=client)
        with pytest.raises(BotNetworkError) as exc_info:
            await bot_client.validate_token("some-token")

    assert "Network error" in str(exc_info.value)


@pytest.mark.asyncio
async def test_send_message_success() -> None:
    """Ensure send_message returns correct SentMessage payload on success."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == "https://api.telegram.org/bot123456:ABC/sendMessage"
        return httpx.Response(
            200,
            json={"ok": True, "result": {"message_id": 555111}},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        bot_client = BotApiClient(client=client)
        sent = await bot_client.send_message(
            token="123456:ABC",
            chat_id="@my_channel",
            text="Hello <b>World</b>",
        )

    assert sent.message_id == 555111


@pytest.mark.asyncio
async def test_send_message_api_failure() -> None:
    """Ensure send_message raises BotApiError when API returns ok=False."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"ok": False, "description": "Chat not found"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        bot_client = BotApiClient(client=client)
        with pytest.raises(BotApiError) as exc_info:
            await bot_client.send_message(
                token="123456:ABC",
                chat_id="@unknown_channel",
                text="Hello",
            )

    assert "ok=false" in str(exc_info.value)
    assert "Chat not found" in str(exc_info.value)
