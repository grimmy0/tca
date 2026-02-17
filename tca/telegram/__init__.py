"""Telegram client management module."""

from .account_loader import TelegramAccountLoader
from .client_manager import (
    AccountLoaderProtocol,
    ClientFactoryProtocol,
    MissingTelethonClientError,
    TelegramAccount,
    TelegramClientProtocol,
    TelethonClientManager,
    TelethonClientManagerError,
)

__all__ = [
    "AccountLoaderProtocol",
    "ClientFactoryProtocol",
    "MissingTelethonClientError",
    "TelegramAccount",
    "TelegramAccountLoader",
    "TelegramClientProtocol",
    "TelethonClientManager",
    "TelethonClientManagerError",
]
