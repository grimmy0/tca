"""Telegram account loader for Telethon client initialization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from tca.auth import TelegramAccountStorage, TelegramSessionStorage

from .client_manager import TelegramAccount

if TYPE_CHECKING:
    from tca.storage.db import SessionFactory


@dataclass(slots=True)
class TelegramAccountLoader:
    """Load Telegram accounts and sessions for client manager startup."""

    read_session_factory: SessionFactory
    write_session_factory: SessionFactory
    key_encryption_key: bytes

    async def __call__(self) -> list[TelegramAccount]:
        """Return decrypted Telegram account credentials with sessions."""
        account_storage = TelegramAccountStorage(
            read_session_factory=self.read_session_factory,
            write_session_factory=self.write_session_factory,
        )
        session_storage = TelegramSessionStorage(
            read_session_factory=self.read_session_factory,
            write_session_factory=self.write_session_factory,
        )
        accounts = await account_storage.list_accounts(
            key_encryption_key=self.key_encryption_key,
        )
        results: list[TelegramAccount] = []
        for account in accounts:
            session_string = await session_storage.load_session(
                account_id=account.account_id,
                key_encryption_key=self.key_encryption_key,
            )
            results.append(
                TelegramAccount(
                    account_id=account.account_id,
                    api_id=account.api_id,
                    api_hash=account.api_hash,
                    string_session=session_string,
                ),
            )
        return results
