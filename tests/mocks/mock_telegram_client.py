"""Mock Telegram client for testing without real network interaction."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, Iterable
from typing import TYPE_CHECKING, Final, Literal, TypeGuard

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

_UNSET_RESPONSE: Final[object] = object()
ResponseKey = Literal[
    "send_code_request",
    "sign_in",
    "get_me",
    "iter_messages",
    "get_messages",
]


class MockTelegramClient:
    """Mock implementation of Telethon's TelegramClient."""

    session: object | None
    api_id: int
    api_hash: str
    _connected: bool
    _authorized: bool
    _me: object | None
    _disconnected_event: asyncio.Event

    def __init__(self, session: object | None, api_id: int, api_hash: str) -> None:
        """Initialize the mock client."""
        self.session = session
        self.api_id = api_id
        self.api_hash = api_hash
        self._connected = False
        self._authorized = False
        self._me = None
        self._disconnected_event = asyncio.Event()
        self._disconnected_event.set()

        # Scripting support
        self.responses: dict[ResponseKey, object] = {
            "send_code_request": _UNSET_RESPONSE,
            "sign_in": _UNSET_RESPONSE,
            "get_me": _UNSET_RESPONSE,
            "iter_messages": _UNSET_RESPONSE,
            "get_messages": _UNSET_RESPONSE,
        }
        self.call_counts: dict[str, int] = {}

    async def connect(self) -> None:
        """Simulate connecting to Telegram."""
        self._mark_call("connect")
        self._connected = True
        self._disconnected_event.clear()

    async def disconnect(self) -> None:
        """Simulate disconnecting from Telegram."""
        self._mark_call("disconnect")
        self._connected = False
        self._disconnected_event.set()

    def is_connected(self) -> bool:
        """Check if the client is 'connected'."""
        return self._connected

    async def is_user_authorized(self) -> bool:
        """Check if the user is 'authorized'."""
        return self._authorized

    async def send_code_request(self, phone: str) -> bool:
        """Simulate sending a code request."""
        _ = phone
        self._mark_call("send_code_request")
        res = self._scripted_response("send_code_request")
        if res is not _UNSET_RESPONSE:
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
        self._mark_call("sign_in")
        res = self._scripted_response("sign_in")
        self._authorized = True
        if res is not _UNSET_RESPONSE:
            return res
        return self._me

    async def get_me(self) -> object:
        """Simulate getting the current user."""
        self._mark_call("get_me")
        res = self._scripted_response("get_me")
        if res is not _UNSET_RESPONSE:
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

    async def iter_messages(
        self,
        entity: object,
        *,
        limit: int | None = None,
    ) -> AsyncIterator[object]:
        """Simulate iterating over channel messages."""
        _ = (entity, limit)
        self._mark_call("iter_messages")
        scripted = self._scripted_response("iter_messages")
        if scripted is _UNSET_RESPONSE:
            return
        if self._is_async_message_stream(scripted):
            async for message in scripted:
                yield message
            return
        if self._is_sync_message_stream(scripted):
            for message in scripted:
                yield message
            return
        yield scripted

    async def get_messages(
        self,
        entity: object,
        limit: int | None = None,
    ) -> list[object]:
        """Simulate fetching a batch of channel messages."""
        _ = (entity, limit)
        self._mark_call("get_messages")
        scripted = self._scripted_response("get_messages")
        if scripted is not _UNSET_RESPONSE:
            return self._normalize_message_batch(scripted)
        return [message async for message in self.iter_messages(entity, limit=limit)]

    async def start(self, *args: object, **kwargs: object) -> None:
        """Simulate starting the client."""
        _ = (args, kwargs)
        self._mark_call("start")
        await self.connect()

    async def run_until_disconnected(self) -> None:
        """Wait until disconnect is called."""
        self._mark_call("run_until_disconnected")
        _ = await self._disconnected_event.wait()

    def _mark_call(self, method: str) -> None:
        """Track call counts for integration assertions."""
        self.call_counts[method] = self.call_counts.get(method, 0) + 1

    def _scripted_response(self, key: ResponseKey) -> object:
        """Return scripted value or raise scripted exception."""
        scripted = self.responses[key]
        if scripted is _UNSET_RESPONSE:
            return _UNSET_RESPONSE
        if isinstance(scripted, Exception):
            raise scripted
        return scripted

    @staticmethod
    def _normalize_message_batch(scripted: object) -> list[object]:
        """Normalize scripted message payload into list form."""
        if MockTelegramClient._is_object_list(scripted):
            return scripted
        if MockTelegramClient._is_object_tuple(scripted):
            return list(scripted)
        return [scripted]

    @staticmethod
    def _is_async_message_stream(value: object) -> TypeGuard[AsyncIterable[object]]:
        """Check whether scripted payload is an async stream of messages."""
        return isinstance(value, AsyncIterable)

    @staticmethod
    def _is_sync_message_stream(value: object) -> TypeGuard[Iterable[object]]:
        """Check whether scripted payload is an iterable message stream."""
        return isinstance(value, Iterable) and not isinstance(
            value,
            (str, bytes, bytearray, dict),
        )

    @staticmethod
    def _is_object_list(value: object) -> TypeGuard[list[object]]:
        """Check whether scripted payload is an explicit list of messages."""
        return isinstance(value, list)

    @staticmethod
    def _is_object_tuple(value: object) -> TypeGuard[tuple[object, ...]]:
        """Check whether scripted payload is a tuple of messages."""
        return isinstance(value, tuple)
