"""Persistence helpers for encrypted Telegram account credentials."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from sqlalchemy import text

from .encryption_utils import decrypt_with_envelope, encrypt_with_envelope

if TYPE_CHECKING:
    from collections.abc import Mapping

    from tca.storage.db import SessionFactory


@dataclass(frozen=True, slots=True)
class TelegramAccountRecord:
    """Typed Telegram account credentials payload."""

    account_id: int
    api_id: int
    api_hash: str
    phone_number: str | None


class TelegramAccountStorageError(RuntimeError):
    """Base exception for account credential storage operations."""

    @classmethod
    def invalid_payload(cls, *, details: str) -> TelegramAccountStorageError:
        """Build deterministic decode failure for account rows."""
        return cls(f"Telegram account payload invalid: {details}")


class TelegramAccountStorage:
    """Repository for encrypting and persisting Telegram account credentials."""

    _read_session_factory: SessionFactory
    _write_session_factory: SessionFactory

    def __init__(
        self,
        *,
        read_session_factory: SessionFactory,
        write_session_factory: SessionFactory,
    ) -> None:
        """Create storage with explicit read/write session dependencies."""
        self._read_session_factory = read_session_factory
        self._write_session_factory = write_session_factory

    async def upsert_account(
        self,
        *,
        api_id: int,
        api_hash: str,
        phone_number: str | None,
        key_encryption_key: bytes,
    ) -> int:
        """Create or update a Telegram account row and return the account id."""
        api_hash_encrypted = encrypt_with_envelope(
            plaintext=api_hash.encode("utf-8"),
            key_encryption_key=key_encryption_key,
        )
        async with self._write_session_factory() as session:
            row = None
            if phone_number is not None:
                result = await session.execute(
                    text(
                        """
                        UPDATE telegram_accounts
                        SET api_id = :api_id,
                            api_hash_encrypted = :api_hash_encrypted,
                            phone_number = :phone_number,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE phone_number = :phone_number
                        RETURNING id
                        """,
                    ),
                    {
                        "api_id": api_id,
                        "api_hash_encrypted": api_hash_encrypted,
                        "phone_number": phone_number,
                    },
                )
                row = result.mappings().one_or_none()

            if row is None:
                result = await session.execute(
                    text(
                        """
                        INSERT INTO telegram_accounts (
                            api_id,
                            api_hash_encrypted,
                            phone_number
                        )
                        VALUES (
                            :api_id,
                            :api_hash_encrypted,
                            :phone_number
                        )
                        RETURNING id
                        """,
                    ),
                    {
                        "api_id": api_id,
                        "api_hash_encrypted": api_hash_encrypted,
                        "phone_number": phone_number,
                    },
                )
                row = result.mappings().one()
            await session.commit()

        row_map = cast("Mapping[str, object]", row)
        return _coerce_int(value=row_map.get("id"), field_name="id")

    async def list_accounts(
        self,
        *,
        key_encryption_key: bytes,
    ) -> list[TelegramAccountRecord]:
        """List stored Telegram accounts with decrypted API hashes."""
        statement = text(
            """
            SELECT id, api_id, api_hash_encrypted, phone_number
            FROM telegram_accounts
            ORDER BY id ASC
            """,
        )
        async with self._read_session_factory() as session:
            result = await session.execute(statement)
            rows = result.mappings().all()
        return [
            _decode_account_row(row, key_encryption_key=key_encryption_key)
            for row in rows
        ]

    async def get_account_id_by_phone_number(
        self,
        *,
        phone_number: str,
    ) -> int | None:
        """Return account id for the phone number or None if missing."""
        statement = text(
            """
            SELECT id
            FROM telegram_accounts
            WHERE phone_number = :phone_number
            """,
        )
        async with self._read_session_factory() as session:
            result = await session.execute(
                statement,
                {"phone_number": phone_number},
            )
            row = result.mappings().one_or_none()
        if row is None:
            return None
        row_map = cast("Mapping[str, object]", row)
        return _coerce_int(value=row_map.get("id"), field_name="id")


def _decode_account_row(
    row: object,
    *,
    key_encryption_key: bytes,
) -> TelegramAccountRecord:
    row_map = cast("Mapping[str, object]", row)
    account_id = _coerce_int(value=row_map.get("id"), field_name="id")
    api_id = _coerce_int(value=row_map.get("api_id"), field_name="api_id")
    api_hash_payload = _coerce_blob_bytes(value=row_map.get("api_hash_encrypted"))
    plaintext = decrypt_with_envelope(
        ciphertext_payload=api_hash_payload,
        key_encryption_key=key_encryption_key,
    )
    try:
        api_hash = plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TelegramAccountStorageError.invalid_payload(
            details="api_hash payload is not valid UTF-8 text",
        ) from exc
    phone_number = _coerce_optional_str(
        value=row_map.get("phone_number"),
        field_name="phone_number",
    )
    return TelegramAccountRecord(
        account_id=account_id,
        api_id=api_id,
        api_hash=api_hash,
        phone_number=phone_number,
    )


def _coerce_int(*, value: object, field_name: str) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise TelegramAccountStorageError.invalid_payload(
        details=f"missing `{field_name}` value",
    )


def _coerce_optional_str(*, value: object, field_name: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise TelegramAccountStorageError.invalid_payload(
        details=f"invalid `{field_name}` value",
    )


def _coerce_blob_bytes(*, value: object) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, memoryview):
        return value.tobytes()
    raise TelegramAccountStorageError.invalid_payload(details="invalid blob payload")
