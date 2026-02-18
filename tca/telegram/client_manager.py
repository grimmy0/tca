"""Telethon client manager for app-scoped lifecycle management."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class TelethonClientManagerError(RuntimeError):
    """Base exception for Telethon client manager operations."""


class MissingTelethonClientError(TelethonClientManagerError):
    """Raised when a requested Telegram client is not available."""

    @classmethod
    def for_account_id(cls, account_id: int) -> MissingTelethonClientError:
        """Build deterministic error for missing client by account id."""
        message = f"No Telethon client is registered for account id={account_id}."
        return cls(message)


@dataclass(frozen=True, slots=True)
class TelegramAccount:
    """Minimal account payload needed to construct a Telethon client."""

    account_id: int
    api_id: int
    api_hash: str
    string_session: str | None = None


class TelegramClientProtocol(Protocol):
    """Minimal Telethon client surface used by the manager."""

    async def connect(self) -> None:
        """Connect to Telegram."""
        ...

    async def disconnect(self) -> None:
        """Disconnect from Telegram."""
        ...

    def is_connected(self) -> bool:
        """Return True when the client is currently connected."""
        ...


class AccountLoaderProtocol(Protocol):
    """Load Telegram accounts that should be registered at startup."""

    async def __call__(self) -> list[TelegramAccount]:
        """Return a list of accounts to register."""
        ...


class ClientFactoryProtocol(Protocol):
    """Factory for constructing Telegram clients from account data."""

    def __call__(self, account: TelegramAccount) -> TelegramClientProtocol:
        """Create a client for the supplied account."""
        ...


async def _empty_account_loader() -> list[TelegramAccount]:
    """Default account loader that registers no Telegram accounts."""
    return []


def _unsupported_client_factory(account: TelegramAccount) -> TelegramClientProtocol:
    """Default client factory that fails if invoked without injection."""
    _ = account
    message = "Telethon client factory must be provided by runtime wiring."
    raise TelethonClientManagerError(message)


def _new_client_map() -> dict[int, TelegramClientProtocol]:
    return {}


@dataclass(slots=True)
class TelethonClientManager:
    """Manage Telethon clients and lifecycle integration."""

    account_loader: AccountLoaderProtocol = field(default=_empty_account_loader)
    client_factory: ClientFactoryProtocol = field(default=_unsupported_client_factory)
    clients: dict[int, TelegramClientProtocol] = field(default_factory=_new_client_map)

    async def startup(self) -> None:
        """Register and connect all configured Telegram clients."""
        accounts = await self.account_loader()
        for account in accounts:
            _ = self._register_account(account)
        for client in self.clients.values():
            if not client.is_connected():
                await client.connect()

    async def shutdown(self) -> None:
        """Disconnect all managed Telegram clients."""
        for client in self.clients.values():
            await client.disconnect()

    def get_client(self, *, account_id: int) -> TelegramClientProtocol:
        """Return an existing client or raise if not registered."""
        client = self.clients.get(account_id)
        if client is None:
            raise MissingTelethonClientError.for_account_id(account_id)
        return client

    def register_account(self, account: TelegramAccount) -> TelegramClientProtocol:
        """Register a client for an account without implicit re-creation."""
        return self._register_account(account)

    def _register_account(self, account: TelegramAccount) -> TelegramClientProtocol:
        """Register an account, reusing an existing client if already present."""
        client = self.clients.get(account.account_id)
        if client is None:
            client = self.client_factory(account)
            self.clients[account.account_id] = client
        return client
