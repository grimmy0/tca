"""Tests for Telegram auth code verification endpoint."""

from __future__ import annotations

import itertools
import sqlite3
from http import HTTPStatus
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from unittest.mock import patch

from fastapi.testclient import TestClient
from telethon.errors import PhoneCodeInvalidError, SessionPasswordNeededError

from tca.api.app import create_app

if TYPE_CHECKING:
    from pathlib import Path

    from tests.mocks.mock_telegram_client import MockTelegramClient

BOOTSTRAP_TOKEN = "telegram-verify-token"  # noqa: S105


def test_verify_code_advances_to_authenticated_state(
    tmp_path: Path,
    monkeypatch: object,
    mock_tg_client: MockTelegramClient,
) -> None:
    """Ensure valid code updates auth session status to authenticated."""
    db_path = _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="telegram-auth-verify.sqlite3",
        output_file_name="telegram-auth-verify-token.txt",
    )
    api_id = 7777
    api_hash = "hash-for-verify"
    phone_number = "+15550004444"

    app = create_app()
    app.state.telegram_auth_client_factory = _build_factory(
        mock_tg_client=mock_tg_client,
        expected_api_id=api_id,
        expected_api_hash=api_hash,
    )

    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            side_effect=_token_side_effect(),
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
    if payload.get("session_id") != session_id:
        raise AssertionError
    if mock_tg_client.call_counts.get("sign_in") != 1:
        raise AssertionError
    if _fetch_session_status(db_path=db_path, session_id=session_id) != "authenticated":
        raise AssertionError


def test_verify_code_requires_password_updates_status(
    tmp_path: Path,
    monkeypatch: object,
    mock_tg_client: MockTelegramClient,
) -> None:
    """Ensure password-required responses transition auth session status."""
    db_path = _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="telegram-auth-password.sqlite3",
        output_file_name="telegram-auth-password-token.txt",
    )
    api_id = 8888
    api_hash = "hash-for-password"
    phone_number = "+15550005555"
    mock_tg_client.responses["sign_in"] = SessionPasswordNeededError(request=None)

    app = create_app()
    app.state.telegram_auth_client_factory = _build_factory(
        mock_tg_client=mock_tg_client,
        expected_api_id=api_id,
        expected_api_hash=api_hash,
    )

    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            side_effect=_token_side_effect(),
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
                "code": "54321",
            },
            headers=_auth_headers(),
        )

    if response.status_code != HTTPStatus.OK:
        raise AssertionError
    payload = response.json()
    if payload.get("status") != "password_required":
        raise AssertionError
    if _fetch_session_status(
        db_path=db_path,
        session_id=session_id,
    ) != "password_required":
        raise AssertionError


def test_verify_code_wrong_code_returns_deterministic_error(
    tmp_path: Path,
    monkeypatch: object,
    mock_tg_client: MockTelegramClient,
) -> None:
    """Ensure invalid codes return deterministic errors and keep status."""
    db_path = _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="telegram-auth-wrong-code.sqlite3",
        output_file_name="telegram-auth-wrong-code-token.txt",
    )
    api_id = 9999
    api_hash = "hash-for-wrong-code"
    phone_number = "+15550006666"
    mock_tg_client.responses["sign_in"] = PhoneCodeInvalidError(request=None)

    app = create_app()
    app.state.telegram_auth_client_factory = _build_factory(
        mock_tg_client=mock_tg_client,
        expected_api_id=api_id,
        expected_api_hash=api_hash,
    )

    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            side_effect=_token_side_effect(),
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
                "code": "99999",
            },
            headers=_auth_headers(),
        )

    if response.status_code != HTTPStatus.BAD_REQUEST:
        raise AssertionError
    payload = response.json()
    if payload.get("detail") != "Invalid Telegram login code.":
        raise AssertionError
    if _fetch_session_status(db_path=db_path, session_id=session_id) != "code_sent":
        raise AssertionError


def test_verify_code_replayed_or_expired_session_returns_failure(
    tmp_path: Path,
    monkeypatch: object,
    mock_tg_client: MockTelegramClient,
) -> None:
    """Ensure replayed or expired sessions fail verification."""
    db_path = _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="telegram-auth-replayed.sqlite3",
        output_file_name="telegram-auth-replayed-token.txt",
    )
    api_id = 4444
    api_hash = "hash-for-replayed"
    phone_number = "+15550007777"

    app = create_app()
    app.state.telegram_auth_client_factory = _build_factory(
        mock_tg_client=mock_tg_client,
        expected_api_id=api_id,
        expected_api_hash=api_hash,
    )

    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            side_effect=_token_side_effect(),
        ),
        TestClient(app) as client,
    ):
        session_id = _start_auth_session(
            client=client,
            api_id=api_id,
            api_hash=api_hash,
            phone_number=phone_number,
        )
        first = client.post(
            "/auth/telegram/verify-code",
            json={
                "session_id": session_id,
                "api_id": api_id,
                "api_hash": api_hash,
                "code": "11111",
            },
            headers=_auth_headers(),
        )
        replayed = client.post(
            "/auth/telegram/verify-code",
            json={
                "session_id": session_id,
                "api_id": api_id,
                "api_hash": api_hash,
                "code": "11111",
            },
            headers=_auth_headers(),
        )

        expired_session_id = _start_auth_session(
            client=client,
            api_id=api_id,
            api_hash=api_hash,
            phone_number=phone_number,
        )
        _expire_session(db_path=db_path, session_id=expired_session_id)
        expired = client.post(
            "/auth/telegram/verify-code",
            json={
                "session_id": expired_session_id,
                "api_id": api_id,
                "api_hash": api_hash,
                "code": "11111",
            },
            headers=_auth_headers(),
        )

    if first.status_code != HTTPStatus.OK:
        raise AssertionError
    if replayed.status_code != HTTPStatus.CONFLICT:
        raise AssertionError
    if expired.status_code != HTTPStatus.GONE:
        raise AssertionError
    replayed_payload = replayed.json()
    if "cannot accept login code" not in str(replayed_payload.get("detail")):
        raise AssertionError
    expired_payload = expired.json()
    expected_detail = (
        f"Auth session state expired for session_id='{expired_session_id}'."
    )
    if expired_payload.get("detail") != expected_detail:
        raise AssertionError


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


def _fetch_session_status(*, db_path: Path, session_id: str) -> str:
    """Fetch auth session status from sqlite storage."""
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            "SELECT status FROM auth_session_state WHERE session_id = ?",
            (session_id,),
        )
        row = cursor.fetchone()
    if row is None or not isinstance(row[0], str):
        raise AssertionError
    return row[0]


def _expire_session(*, db_path: Path, session_id: str) -> None:
    """Force session expiry by writing a past timestamp."""
    with sqlite3.connect(db_path) as connection:
        _ = connection.execute(
            "UPDATE auth_session_state SET expires_at = 1 WHERE session_id = ?",
            (session_id,),
        )
        connection.commit()


def _token_side_effect() -> object:
    """Return a token generator that reserves the first value for auth."""
    counter = itertools.count()

    def _next(*_args: object, **_kwargs: object) -> str:
        index = next(counter)
        if index == 0:
            return BOOTSTRAP_TOKEN
        return f"telegram-session-{index}"

    return _next


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
) -> Path:
    """Set DB/token-output env vars for authenticated API tests."""
    patcher = _as_monkeypatch(monkeypatch)
    db_path = tmp_path / db_name
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (tmp_path / output_file_name).as_posix(),
    )
    return db_path


def _auth_headers() -> dict[str, str]:
    """Build deterministic Authorization header for auth API tests."""
    return {"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"}


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
