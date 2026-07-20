"""Telegram Bot API Client implementation using httpx."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(slots=True, frozen=True)
class BotInfo:
    """Information returned about the Telegram Bot."""

    bot_id: int
    username: str


@dataclass(slots=True, frozen=True)
class SentMessage:
    """Payload of a successfully sent Telegram message."""

    message_id: int


class BotApiError(RuntimeError):
    """Base exception for all Telegram Bot API errors."""


class BotTokenInvalidError(BotApiError):
    """Raised when the Telegram token is invalid (HTTP 401)."""


class BotNetworkError(BotApiError):
    """Raised when network failures or timeouts occur."""


class BotApiClient:
    """Stateless Telegram Bot API client using httpx.AsyncClient."""

    _client: httpx.AsyncClient

    def __init__(self, *, client: httpx.AsyncClient | None = None) -> None:
        """Initialize client, optionally sharing an existing httpx.AsyncClient."""
        self._client = client or httpx.AsyncClient()

    async def validate_token(self, token: str) -> BotInfo:
        """Call getMe to validate the token and retrieve the bot's username."""
        url = f"https://api.telegram.org/bot{token}/getMe"
        try:
            response = await self._client.get(url)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                msg = f"Invalid bot token (401 Unauthorized): {exc.response.text}"
                raise BotTokenInvalidError(msg) from exc
            msg = f"Telegram API error {exc.response.status_code}: {exc.response.text}"
            raise BotApiError(msg) from exc
        except httpx.RequestError as exc:
            msg = f"Network error connecting to Telegram API: {exc}"
            raise BotNetworkError(msg) from exc

        try:
            data = response.json()
        except ValueError as exc:
            msg = f"Invalid JSON response from Telegram API: {response.text}"
            raise BotApiError(msg) from exc

        if not isinstance(data, dict) or not data.get("ok"):
            desc = data.get("description") if isinstance(data, dict) else "Unknown error"
            msg = f"Telegram API returned ok=false: {desc}"
            raise BotApiError(msg)

        result = data.get("result")
        if not isinstance(result, dict):
            msg = f"Unexpected result format from Telegram API: {result}"
            raise BotApiError(msg)

        bot_id = result.get("id")
        username = result.get("username")
        if not isinstance(bot_id, int) or not isinstance(username, str):
            msg = f"Missing required fields id/username in result: {result}"
            raise BotApiError(msg)

        return BotInfo(bot_id=bot_id, username=username)

    async def send_message(
        self,
        token: str,
        chat_id: str,
        text: str,
        parse_mode: str = "HTML",
    ) -> SentMessage:
        """Send a message using sendMessage API."""
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        try:
            response = await self._client.post(url, json=payload)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                msg = f"Invalid bot token (401 Unauthorized): {exc.response.text}"
                raise BotTokenInvalidError(msg) from exc
            msg = f"Telegram API error {exc.response.status_code}: {exc.response.text}"
            raise BotApiError(msg) from exc
        except httpx.RequestError as exc:
            msg = f"Network error connecting to Telegram API: {exc}"
            raise BotNetworkError(msg) from exc

        try:
            data = response.json()
        except ValueError as exc:
            msg = f"Invalid JSON response from Telegram API: {response.text}"
            raise BotApiError(msg) from exc

        if not isinstance(data, dict) or not data.get("ok"):
            desc = data.get("description") if isinstance(data, dict) else "Unknown error"
            msg = f"Telegram API returned ok=false: {desc}"
            raise BotApiError(msg)

        result = data.get("result")
        if not isinstance(result, dict):
            msg = f"Unexpected result format from Telegram API: {result}"
            raise BotApiError(msg)

        message_id = result.get("message_id")
        if not isinstance(message_id, int):
            msg = f"Missing required message_id in result: {result}"
            raise BotApiError(msg)

        return SentMessage(message_id=message_id)
