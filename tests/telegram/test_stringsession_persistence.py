"""Tests for persisting Telegram StringSession after successful login."""

from __future__ import annotations

import asyncio
import sqlite3
from http import HTTPStatus
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from unittest.mock import patch

from fastapi.testclient import TestClient

from tca.api.app import create_app
from tca.auth import UnlockState, resolve_key_encryption_key
from tca.config.settings import load_settings
from tca.storage import WriterQueue, create_storage_runtime, dispose_storage_runtime
from tca.storage.settings_repo import SettingsRepository
from tca.telegram import TelegramAccountLoader, TelethonClientManager
from tests.mocks.mock_telegram_client import MockTelegramClient

if TYPE_CHECKING:
    from pathlib import Path

BOOTSTRAP_TOKEN = "telegram-session-persist-token"  # noqa: S105


def test_stringsession_persisted_and_reused(
    tmp_path: Path,
    monkeypatch: object,
    mock_tg_client: MockTelegramClient,
) -> None:
    """Ensure session is stored encrypted and reused for later client init."""
    db_path, passphrase = _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="telegram-auth-persist.sqlite3",
        output_file_name="telegram-auth-persist-token.txt",
    )
    api_id = 13579
    api_hash = "hash-for-session-persist"
    phone_number = "+15550007777"
    expected_session = "1AABBCCDDEE-session-persist-2f6c1d0b3b8f4b0f9fbed322"
    mock_tg_client.session = _FakeStringSession(expected_session)

    app = create_app()
    app.state.telegram_auth_client_factory = _build_factory(
        mock_tg_client=mock_tg_client,
        expected_api_id=api_id,
        expected_api_hash=api_hash,
    )

    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        session_id = _start_auth_session(
            client=client,
            api_id=api_id,
            api_hash=api_hash,
            phone_number=phone_number,
        )
        response = client.post(
            "/auth/telegram/verify-code",
            json={
                "session_id": session_id,
                "api_id": api_id,
                "api_hash": api_hash,
                "code": "12345",
            },
            headers=_auth_headers(),
        )

    if response.status_code != HTTPStatus.OK:
        raise AssertionError
    payload = response.json()
    if payload.get("status") != "authenticated":
        raise AssertionError

    row = _fetch_account_row(db_path=db_path)
    if row["api_id"] != api_id:
        raise AssertionError
    if row["phone_number"] != phone_number:
        raise AssertionError
    stored_payload = row["session_encrypted"]
    if stored_payload is None:
        raise AssertionError
    if stored_payload == expected_session.encode("utf-8"):
        raise AssertionError

    accounts = asyncio.run(
        _load_accounts(
            db_path=db_path,
            passphrase=passphrase,
        ),
    )
    if len(accounts) != 1:
        raise AssertionError
    if accounts[0].string_session != expected_session:
        raise AssertionError

    created_sessions: list[str | None] = []

    def _factory(account: object) -> MockTelegramClient:
        if not isinstance(account, _AccountLike):
            raise AssertionError
        created_sessions.append(account.string_session)
        return MockTelegramClient(
            session=account.string_session,
            api_id=account.api_id,
            api_hash=account.api_hash,
        )

    async def _load() -> list[object]:
        return accounts

    manager = TelethonClientManager(
        account_loader=_load,
        client_factory=_factory,
    )
    asyncio.run(_exercise_manager(manager))

    if created_sessions != [expected_session]:
        raise AssertionError


async def _load_accounts(*, db_path: Path, passphrase: str) -> list[object]:
    settings = load_settings({"TCA_DB_PATH": db_path.as_posix()})
    runtime = create_storage_runtime(settings)
    writer_queue = WriterQueue()
    unlock_state = UnlockState()
    unlock_state.unlock_with_passphrase(passphrase=passphrase)
    settings_repo = SettingsRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )
    try:
        key_encryption_key = await resolve_key_encryption_key(
            settings_repository=settings_repo,
            writer_queue=writer_queue,
            unlock_state=unlock_state,
        )
        loader = TelegramAccountLoader(
            read_session_factory=runtime.read_session_factory,
            write_session_factory=runtime.write_session_factory,
            key_encryption_key=key_encryption_key,
        )
        return await loader()
    finally:
        await writer_queue.close()
        await dispose_storage_runtime(runtime)


async def _exercise_manager(manager: TelethonClientManager) -> None:
    await manager.startup()
    await manager.shutdown()


def _fetch_account_row(*, db_path: Path) -> dict[str, object]:
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        cursor = connection.execute(
            """
            SELECT api_id, phone_number, session_encrypted
            FROM telegram_accounts
            LIMIT 1
            """,
        )
        row = cursor.fetchone()
    if row is None:
        raise AssertionError
    return dict(row)


def _build_factory(
    *,
    mock_tg_client: MockTelegramClient,
    expected_api_id: int,
    expected_api_hash: str,
) -> object:
    """Build client factory that asserts inputs and returns the mock client."""

    def _factory(api_id: int, api_hash: str) -> MockTelegramClient:
        if api_id != expected_api_id:
            raise AssertionError
        if api_hash != expected_api_hash:
            raise AssertionError
        return mock_tg_client

    return _factory


def _configure_auth_env(
    *,
    tmp_path: Path,
    monkeypatch: object,
    db_name: str,
    output_file_name: str,
) -> tuple[Path, str]:
    """Set DB/token-output env vars for authenticated API tests."""
    patcher = _as_monkeypatch(monkeypatch)
    db_path = tmp_path / db_name
    secret_file = tmp_path / "auth-persist.secret"
    passphrase = "auth-persist-secret"  # noqa: S105
    _ = secret_file.write_text(f"{passphrase}\n", encoding="utf-8")
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (tmp_path / output_file_name).as_posix(),
    )
    patcher.setenv("TCA_MODE", "auto-unlock")
    patcher.setenv("TCA_SECRET_FILE", secret_file.as_posix())
    return db_path, passphrase


def _auth_headers() -> dict[str, str]:
    """Build deterministic Authorization header for auth API tests."""
    return {"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"}


def _start_auth_session(
    *,
    client: TestClient,
    api_id: int,
    api_hash: str,
    phone_number: str,
) -> str:
    """Start Telegram auth to obtain a session id."""
    response = client.post(
        "/auth/telegram/start",
        json={
            "api_id": api_id,
            "api_hash": api_hash,
            "phone_number": phone_number,
        },
        headers=_auth_headers(),
    )
    if response.status_code != HTTPStatus.CREATED:
        raise AssertionError
    payload = response.json()
    session_id = payload.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise AssertionError
    return session_id


def _as_monkeypatch(value: object) -> MonkeyPatchLike:
    """Narrow monkeypatch fixture object to setenv-capable helper."""
    if not isinstance(value, MonkeyPatchLike):
        raise TypeError
    return value


@runtime_checkable
class MonkeyPatchLike(Protocol):
    """Runtime-checkable subset of pytest monkeypatch fixture behavior."""

    def setenv(self, name: str, value: str) -> None:
        """Set environment variable for duration of current test."""


@runtime_checkable
class _AccountLike(Protocol):
    """Runtime-checkable account payload for client factory assertions."""

    account_id: int
    api_id: int
    api_hash: str
    string_session: str | None


class _FakeStringSession:
    """Minimal StringSession stand-in with a deterministic save method."""

    def __init__(self, value: str) -> None:
        self._value = value

    def save(self) -> str:
        """Return fixed session payload."""
        return self._value
