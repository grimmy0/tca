"""Tests for encrypted Telegram session persistence in `telegram_accounts`."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy import text

from tca.auth import (
    DATA_ENCRYPTION_KEY_BYTES,
    EnvelopeDecryptionError,
    TelegramSessionStorage,
)
from tca.config.settings import load_settings
from tca.storage import StorageRuntime, create_storage_runtime, dispose_storage_runtime

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping
    from pathlib import Path

DEFAULT_ACCOUNT_ID = 1


@pytest.fixture
async def session_storage_runtime(
    tmp_path: Path,
) -> AsyncIterator[tuple[TelegramSessionStorage, StorageRuntime]]:
    """Create isolated schema fixture for encrypted session storage tests."""
    db_path = tmp_path / "session-storage.sqlite3"
    settings = load_settings({"TCA_DB_PATH": db_path.as_posix()})
    runtime = create_storage_runtime(settings)

    async with runtime.write_engine.begin() as connection:
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS telegram_accounts (
                id INTEGER PRIMARY KEY,
                api_id INTEGER NOT NULL,
                api_hash_encrypted BLOB NOT NULL,
                session_encrypted BLOB NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """,
        )

    async with runtime.write_session_factory() as session:
        _ = await session.execute(
            text(
                """
                INSERT INTO telegram_accounts (id, api_id, api_hash_encrypted)
                VALUES (:id, :api_id, :api_hash_encrypted)
                """,
            ),
            {
                "id": DEFAULT_ACCOUNT_ID,
                "api_id": 12345,
                "api_hash_encrypted": b"api-hash-ciphertext",
            },
        )
        await session.commit()

    try:
        yield (
            TelegramSessionStorage(
                read_session_factory=runtime.read_session_factory,
                write_session_factory=runtime.write_session_factory,
            ),
            runtime,
        )
    finally:
        await dispose_storage_runtime(runtime)


@pytest.mark.asyncio
async def test_stored_session_data_is_encrypted_not_plaintext_stringsession(
    session_storage_runtime: tuple[TelegramSessionStorage, StorageRuntime],
) -> None:
    """Ensure persisted blob is ciphertext payload, not plaintext StringSession."""
    storage, runtime = session_storage_runtime
    key_encryption_key = secrets.token_bytes(DATA_ENCRYPTION_KEY_BYTES)
    string_session = (
        "1AABBCCDDEE-local-session-material-with-entropy-"
        "8f9e6d44b40f4de0ac4b9c84f00f2d17"
    )

    await storage.persist_session(
        account_id=DEFAULT_ACCOUNT_ID,
        string_session=string_session,
        key_encryption_key=key_encryption_key,
    )

    async with runtime.read_session_factory() as session:
        result = await session.execute(
            text(
                """
                SELECT session_encrypted
                FROM telegram_accounts
                WHERE id = :account_id
                """,
            ),
            {"account_id": DEFAULT_ACCOUNT_ID},
        )
        row_map = cast(
            "Mapping[str, object]",
            cast("object", result.mappings().one()),
        )
        stored_payload_obj = row_map.get("session_encrypted")

    if not isinstance(stored_payload_obj, bytes):
        raise TypeError
    stored_payload = stored_payload_obj
    if stored_payload == string_session.encode("utf-8"):
        raise AssertionError


@pytest.mark.asyncio
async def test_session_round_trip_through_db_decrypts_correctly(
    session_storage_runtime: tuple[TelegramSessionStorage, StorageRuntime],
) -> None:
    """Ensure persisted encrypted session round-trips through DB decrypt path."""
    storage, _ = session_storage_runtime
    key_encryption_key = secrets.token_bytes(DATA_ENCRYPTION_KEY_BYTES)
    expected_session = "1AABBCCDDEE-round-trip-session-9d8a9f6f5b64439aac174383753d9f2a"

    await storage.persist_session(
        account_id=DEFAULT_ACCOUNT_ID,
        string_session=expected_session,
        key_encryption_key=key_encryption_key,
    )
    loaded_session = await storage.load_session(
        account_id=DEFAULT_ACCOUNT_ID,
        key_encryption_key=key_encryption_key,
    )

    if loaded_session != expected_session:
        raise AssertionError


@pytest.mark.asyncio
async def test_incorrect_kek_prevents_session_load(
    session_storage_runtime: tuple[TelegramSessionStorage, StorageRuntime],
) -> None:
    """Ensure decrypt attempt fails when caller provides incorrect KEK bytes."""
    storage, _ = session_storage_runtime
    correct_key_encryption_key = secrets.token_bytes(DATA_ENCRYPTION_KEY_BYTES)
    wrong_key_encryption_key = secrets.token_bytes(DATA_ENCRYPTION_KEY_BYTES)
    session_value = "1AABBCCDDEE-wrong-kek-test-5f5b90702a2040b0a17d2a5d93fcae12"

    await storage.persist_session(
        account_id=DEFAULT_ACCOUNT_ID,
        string_session=session_value,
        key_encryption_key=correct_key_encryption_key,
    )

    with pytest.raises(
        EnvelopeDecryptionError,
        match="unable to decrypt payload with provided key-encryption key",
    ):
        _ = await storage.load_session(
            account_id=DEFAULT_ACCOUNT_ID,
            key_encryption_key=wrong_key_encryption_key,
        )
