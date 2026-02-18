"""Tests for Telegram auth password verification endpoint."""

from __future__ import annotations

import itertools
import sqlite3
import time
from http import HTTPStatus
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from unittest.mock import patch

from fastapi.testclient import TestClient
from telethon.errors import PasswordHashInvalidError, SessionPasswordNeededError

from tca.api.app import create_app
from tca.auth import SENSITIVE_OPERATION_LOCKED_MESSAGE

if TYPE_CHECKING:
    from pathlib import Path

    from tests.mocks.mock_telegram_client import MockTelegramClient

BOOTSTRAP_TOKEN = "telegram-password-token"  # noqa: S105


def test_verify_password_finalizes_login(
    tmp_path: Path,
    monkeypatch: object,
    mock_tg_client: MockTelegramClient,
) -> None:
    """Ensure valid password updates auth session status to authenticated."""
    db_path = _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="telegram-auth-verify-password.sqlite3",
        output_file_name="telegram-auth-verify-password-token.txt",
    )
    api_id = 4242
    api_hash = "hash-for-password"
    phone_number = "+15550009999"
    password = "correct-password"  # noqa: S105
    expected_session = "telegram-password-session"
    mock_tg_client.session = _FakeStringSession(expected_session)
    mock_tg_client.responses["sign_in"] = SessionPasswordNeededError(request=None)

    app = create_app()
    session_strings: list[str | None] = []
    app.state.telegram_auth_client_factory = _build_factory(
        mock_tg_client=mock_tg_client,
        expected_api_id=api_id,
        expected_api_hash=api_hash,
        session_strings=session_strings,
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
        if response.json().get("status") != "password_required":
            raise AssertionError

        mock_tg_client.responses["sign_in"] = object()
        password_response = client.post(
            "/auth/telegram/verify-password",
            json={
                "session_id": session_id,
                "api_id": api_id,
                "api_hash": api_hash,
                "password": password,
            },
            headers=_auth_headers(),
        )

    if password_response.status_code != HTTPStatus.OK:
        raise AssertionError
    payload = password_response.json()
    if payload.get("status") != "authenticated":
        raise AssertionError
    if payload.get("session_id") != session_id:
        raise AssertionError
    if mock_tg_client.call_counts.get("sign_in") != 2:  # noqa: PLR2004
        raise AssertionError
    if expected_session not in session_strings:
        raise AssertionError
    if _fetch_session_status(db_path=db_path, session_id=session_id) != "authenticated":
        raise AssertionError
    if (
        _fetch_session_telegram_session(
            db_path=db_path,
            session_id=session_id,
        )
        is not None
    ):
        raise AssertionError


def test_verify_password_wrong_password_returns_retryable_error(
    tmp_path: Path,
    monkeypatch: object,
    mock_tg_client: MockTelegramClient,
) -> None:
    """Ensure invalid passwords return deterministic errors and keep status."""
    db_path = _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="telegram-auth-wrong-password.sqlite3",
        output_file_name="telegram-auth-wrong-password-token.txt",
    )
    api_id = 1414
    api_hash = "hash-for-wrong-password"
    phone_number = "+15550008888"
    mock_tg_client.session = _FakeStringSession("telegram-password-bad")
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
                "code": "55555",
            },
            headers=_auth_headers(),
        )
        if response.status_code != HTTPStatus.OK:
            raise AssertionError
        if response.json().get("status") != "password_required":
            raise AssertionError

        mock_tg_client.responses["sign_in"] = PasswordHashInvalidError(request=None)
        password_response = client.post(
            "/auth/telegram/verify-password",
            json={
                "session_id": session_id,
                "api_id": api_id,
                "api_hash": api_hash,
                "password": "bad-password",
            },
            headers=_auth_headers(),
        )

    if password_response.status_code != HTTPStatus.BAD_REQUEST:
        raise AssertionError
    payload = password_response.json()
    if payload.get("detail") != "Invalid Telegram password.":
        raise AssertionError
    if (
        _fetch_session_status(
            db_path=db_path,
            session_id=session_id,
        )
        != "password_required"
    ):
        raise AssertionError


def test_verify_password_rejects_when_step_not_required(
    tmp_path: Path,
    monkeypatch: object,
    mock_tg_client: MockTelegramClient,
) -> None:
    """Ensure password verification fails when session is not ready."""
    db_path = _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="telegram-auth-password-not-required.sqlite3",
        output_file_name="telegram-auth-password-not-required-token.txt",
    )
    api_id = 2727
    api_hash = "hash-for-not-required"
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
        password_response = client.post(
            "/auth/telegram/verify-password",
            json={
                "session_id": session_id,
                "api_id": api_id,
                "api_hash": api_hash,
                "password": "unused",
            },
            headers=_auth_headers(),
        )

    if password_response.status_code != HTTPStatus.CONFLICT:
        raise AssertionError
    payload = password_response.json()
    if "cannot accept password" not in str(payload.get("detail")):
        raise AssertionError
    if mock_tg_client.call_counts.get("sign_in") is not None:
        raise AssertionError
    if _fetch_session_status(db_path=db_path, session_id=session_id) != "code_sent":
        raise AssertionError


def test_verify_password_rejects_when_sensitive_operations_locked(
    tmp_path: Path,
    monkeypatch: object,
    mock_tg_client: MockTelegramClient,
) -> None:
    """Ensure locked mode blocks password verification before sign-in."""
    db_path = _configure_locked_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="telegram-auth-locked-password.sqlite3",
        output_file_name="telegram-auth-locked-password-token.txt",
    )
    api_id = 3030
    api_hash = "hash-for-locked-password"
    phone_number = "+15550003333"
    session_id = "telegram-locked-password"
    telegram_session = "locked-telegram-session"

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
        _insert_auth_session_state(
            db_path=db_path,
            session_id=session_id,
            phone_number=phone_number,
            status="password_required",
            telegram_session=telegram_session,
        )
        response = client.post(
            "/auth/telegram/verify-password",
            json={
                "session_id": session_id,
                "api_id": api_id,
                "api_hash": api_hash,
                "password": "locked-password",
            },
            headers=_auth_headers(),
        )

    if response.status_code != HTTPStatus.LOCKED:
        raise AssertionError
    payload = response.json()
    if payload.get("detail") != SENSITIVE_OPERATION_LOCKED_MESSAGE:
        raise AssertionError
    if mock_tg_client.call_counts.get("sign_in", 0) != 0:
        raise AssertionError
    if (
        _fetch_session_status(
            db_path=db_path,
            session_id=session_id,
        )
        != "password_required"
    ):
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


def _fetch_session_telegram_session(*, db_path: Path, session_id: str) -> str | None:
    """Fetch auth session Telegram session from sqlite storage."""
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            "SELECT telegram_session FROM auth_session_state WHERE session_id = ?",
            (session_id,),
        )
        row = cursor.fetchone()
    if row is None:
        raise AssertionError
    if row[0] is None:
        return None
    if not isinstance(row[0], str):
        raise AssertionError  # noqa: TRY004
    return row[0]


def _insert_auth_session_state(
    *,
    db_path: Path,
    session_id: str,
    phone_number: str,
    status: str,
    telegram_session: str | None,
) -> None:
    """Insert a session row directly for locked-mode verification tests."""
    expires_at = int(time.time()) + 900
    with sqlite3.connect(db_path) as connection:
        _ = connection.execute(
            """
            INSERT INTO auth_session_state (
                session_id,
                phone_number,
                status,
                expires_at,
                telegram_session
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (session_id, phone_number, status, expires_at, telegram_session),
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
    session_strings: list[str | None] | None = None,
) -> object:
    """Build client factory that asserts inputs and returns the mock client."""

    def _factory(
        api_id: int,
        api_hash: str,
        session_string: str | None = None,
    ) -> MockTelegramClient:
        if api_id != expected_api_id:
            raise AssertionError
        if api_hash != expected_api_hash:
            raise AssertionError
        if session_strings is not None:
            session_strings.append(session_string)
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
    secret_file = tmp_path / "auth-verify-password.secret"
    _ = secret_file.write_text("auth-verify-password-secret\n", encoding="utf-8")
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (tmp_path / output_file_name).as_posix(),
    )
    patcher.setenv("TCA_MODE", "auto-unlock")
    patcher.setenv("TCA_SECRET_FILE", secret_file.as_posix())
    return db_path


def _configure_locked_auth_env(
    *,
    tmp_path: Path,
    monkeypatch: object,
    db_name: str,
    output_file_name: str,
) -> Path:
    """Set DB/token-output env vars for locked-mode API tests."""
    patcher = _as_monkeypatch(monkeypatch)
    db_path = tmp_path / db_name
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (tmp_path / output_file_name).as_posix(),
    )
    patcher.setenv("TCA_MODE", "secure-interactive")
    patcher.setenv("TCA_SECRET_FILE", "")
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


class _FakeStringSession:
    """Minimal StringSession stand-in with a deterministic save method."""

    def __init__(self, value: str) -> None:
        self._value = value

    def save(self) -> str:
        """Return fixed session payload."""
        return self._value
