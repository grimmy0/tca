"""Persistence helpers for encrypted Telegram `StringSession` material."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from sqlalchemy import text

from .encryption_utils import decrypt_with_envelope, encrypt_with_envelope

if TYPE_CHECKING:
    from collections.abc import Mapping

    from tca.storage.db import SessionFactory


class TelegramSessionStorageError(RuntimeError):
    """Base exception for encrypted Telegram session persistence operations."""

    @classmethod
    def non_utf8_payload(cls) -> TelegramSessionStorageError:
        """Build deterministic decode error for non-UTF-8 payload bytes."""
        message = "Stored Telegram session payload is not valid UTF-8 text."
        return cls(message)

    @classmethod
    def non_bytes_payload(cls) -> TelegramSessionStorageError:
        """Build deterministic decode error for non-bytes payload values."""
        message = "Stored Telegram session payload is not bytes."
        return cls(message)


class TelegramAccountNotFoundError(TelegramSessionStorageError):
    """Raised when session operations target a missing `telegram_accounts` row."""

    @classmethod
    def for_account_id(cls, account_id: int) -> TelegramAccountNotFoundError:
        """Build deterministic missing-account error with account context."""
        message = (
            "Unable to persist Telegram session: no account row exists for "
            f"id={account_id}."
        )
        return cls(message)


class TelegramSessionStorage:
    """Repository for encrypting and persisting Telegram StringSession blobs."""

    _read_session_factory: SessionFactory
    _write_session_factory: SessionFactory

    def __init__(
        self,
        *,
        read_session_factory: SessionFactory,
        write_session_factory: SessionFactory,
    ) -> None:
        """Create session storage with explicit read/write session dependencies."""
        self._read_session_factory = read_session_factory
        self._write_session_factory = write_session_factory

    async def persist_session(
        self,
        *,
        account_id: int,
        string_session: str,
        key_encryption_key: bytes,
    ) -> None:
        """Encrypt and persist StringSession material for a Telegram account row."""
        ciphertext_payload = encrypt_with_envelope(
            plaintext=string_session.encode("utf-8"),
            key_encryption_key=key_encryption_key,
        )
        statement = text(
            """
            UPDATE telegram_accounts
            SET session_encrypted = :session_encrypted,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :account_id
            RETURNING id
            """,
        )
        async with self._write_session_factory() as session:
            result = await session.execute(
                statement,
                {
                    "account_id": account_id,
                    "session_encrypted": ciphertext_payload,
                },
            )
            row = result.mappings().one_or_none()
            await session.commit()
        if row is None:
            raise TelegramAccountNotFoundError.for_account_id(account_id)

    async def load_session(
        self,
        *,
        account_id: int,
        key_encryption_key: bytes,
    ) -> str | None:
        """Load and decrypt StringSession for an account, returning None if unset."""
        statement = text(
            """
            SELECT session_encrypted
            FROM telegram_accounts
            WHERE id = :account_id
            """,
        )
        async with self._read_session_factory() as session:
            result = await session.execute(statement, {"account_id": account_id})
            row = result.mappings().one_or_none()
        if row is None:
            raise TelegramAccountNotFoundError.for_account_id(account_id)

        row_map = cast("Mapping[str, object]", cast("object", row))
        session_encrypted_obj = row_map.get("session_encrypted")
        if session_encrypted_obj is None:
            return None

        ciphertext_payload = _coerce_blob_bytes(value=session_encrypted_obj)
        plaintext = decrypt_with_envelope(
            ciphertext_payload=ciphertext_payload,
            key_encryption_key=key_encryption_key,
        )
        try:
            return plaintext.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise TelegramSessionStorageError.non_utf8_payload() from exc


def _coerce_blob_bytes(*, value: object) -> bytes:
    """Normalize SQLite BLOB payload variants to plain bytes."""
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    raise TelegramSessionStorageError.non_bytes_payload()
