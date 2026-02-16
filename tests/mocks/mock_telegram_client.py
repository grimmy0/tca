"""Mock Telegram client for testing without real network interaction."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class MockTelegramClient:
    """Mock implementation of Telethon's TelegramClient."""

    session: object | None
    api_id: int
    api_hash: str
    _connected: bool
    _authorized: bool
    _me: object | None

    def __init__(self, session: object | None, api_id: int, api_hash: str) -> None:
        """Initialize the mock client."""
        self.session = session
        self.api_id = api_id
        self.api_hash = api_hash
        self._connected = False
        self._authorized = False
        self._me = None

        # Scripting support
        self.responses: dict[str, object] = {
            "send_code_request": None,
            "sign_in": None,
            "get_me": None,
        }

    async def connect(self) -> None:
        """Simulate connecting to Telegram."""
        self._connected = True

    async def disconnect(self) -> None:
        """Simulate disconnecting from Telegram."""
        self._connected = False

    def is_connected(self) -> bool:
        """Check if the client is 'connected'."""
        return self._connected

    async def is_user_authorized(self) -> bool:
        """Check if the user is 'authorized'."""
        return self._authorized

    async def send_code_request(self, phone: str) -> bool:
        """Simulate sending a code request."""
        _ = phone
        if self.responses["send_code_request"]:
            res = self.responses["send_code_request"]
            if isinstance(res, Exception):
                raise res
            return bool(res)
        return True

    async def sign_in(
        self,
        phone: str | None = None,
        code: str | None = None,
        password: str | None = None,
        bot_token: str | None = None,
    ) -> object:
        """Simulate signing in."""
        _ = (phone, code, password, bot_token)
        if self.responses["sign_in"]:
            res = self.responses["sign_in"]
            if isinstance(res, Exception):
                raise res
            self._authorized = True
            return res
        self._authorized = True
        return self._me

    async def get_me(self) -> object:
        """Simulate getting the current user."""
        if self.responses["get_me"]:
            res = self.responses["get_me"]
            if isinstance(res, Exception):
                raise res
            return res
        return self._me

    def on(
        self,
        event: object,
    ) -> Callable[[Callable[..., object]], Callable[..., object]]:
        """Simulate the event decorator."""
        _ = event

        def decorator(f: Callable[..., object]) -> Callable[..., object]:
            return f

        return decorator

    async def start(self, *args: object, **kwargs: object) -> None:
        """Simulate starting the client."""
        _ = (args, kwargs)
        await self.connect()

    async def run_until_disconnected(self) -> None:
        """Simulate the client running loop."""
        while self._connected:
            await asyncio.sleep(0)
            if not self._connected:
                break
            # In a real mock we might use an Event, but this is a simple stub
            await asyncio.sleep(0.1)
