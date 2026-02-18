"""Tests for first-run setup wizard UI flow (unlock + Telegram auth)."""

from __future__ import annotations

import itertools
import re
import sqlite3
from http import HTTPStatus
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from unittest.mock import patch

from fastapi.testclient import TestClient

from tca.api.app import create_app

if TYPE_CHECKING:
    from pathlib import Path

    from tests.mocks.mock_telegram_client import MockTelegramClient

BOOTSTRAP_TOKEN = "ui-setup-bootstrap-token"  # noqa: S105


def test_setup_wizard_follows_required_step_order(
    tmp_path: Path,
    monkeypatch: object,
    mock_tg_client: MockTelegramClient,
) -> None:
    """Ensure unlock -> auth start -> OTP step order is enforced in rendered UI."""
    _configure_locked_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="ui-setup-order.sqlite3",
        output_file_name="ui-setup-order-token.txt",
    )
    api_id = 1234
    api_hash = "wizard-order-hash"
    phone_number = "+15550000001"

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
        initial = client.get("/ui/setup", headers=_auth_headers())
        unlocked = client.post(
            "/ui/setup/unlock",
            data={"passphrase": "wizard-passphrase"},
            headers=_auth_headers(),
        )
        started = client.post(
            "/ui/setup/start-auth",
            data={
                "api_id": str(api_id),
                "api_hash": api_hash,
                "phone_number": phone_number,
            },
            headers=_auth_headers(),
        )

    if initial.status_code != HTTPStatus.OK:
        raise AssertionError
    if "Setup Step 1: Unlock" not in initial.text:
        raise AssertionError
    if unlocked.status_code != HTTPStatus.OK:
        raise AssertionError
    if "Setup Step 2: Telegram Credentials" not in unlocked.text:
        raise AssertionError
    if started.status_code != HTTPStatus.OK:
        raise AssertionError
    if "Setup Step 3: OTP Verification" not in started.text:
        raise AssertionError
    if not _extract_hidden_value(body=started.text, input_name="session_id"):
        raise AssertionError


def test_setup_wizard_blocks_invalid_step_transition(
    tmp_path: Path,
    monkeypatch: object,
    mock_tg_client: MockTelegramClient,
) -> None:
    """Ensure start-auth is rejected while unlock step has not been completed."""
    _configure_locked_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="ui-setup-invalid-transition.sqlite3",
        output_file_name="ui-setup-invalid-transition-token.txt",
    )
    api_id = 1234
    api_hash = "wizard-invalid-hash"
    phone_number = "+15550000002"

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
        blocked = client.post(
            "/ui/setup/start-auth",
            data={
                "api_id": str(api_id),
                "api_hash": api_hash,
                "phone_number": phone_number,
            },
            headers=_auth_headers(),
        )

    if blocked.status_code != HTTPStatus.CONFLICT:
        raise AssertionError
    if "Setup step transition is invalid." not in blocked.text:
        raise AssertionError


def test_setup_wizard_success_persists_account_and_exits_setup_mode(
    tmp_path: Path,
    monkeypatch: object,
    mock_tg_client: MockTelegramClient,
) -> None:
    """Ensure successful setup persists account and redirects away from setup mode."""
    db_path = _configure_locked_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="ui-setup-success.sqlite3",
        output_file_name="ui-setup-success-token.txt",
    )
    api_id = 4321
    api_hash = "wizard-success-hash"
    phone_number = "+15550000003"
    mock_tg_client.session = _FakeStringSession("wizard-success-session")

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
        _ = client.post(
            "/ui/setup/unlock",
            data={"passphrase": "wizard-passphrase"},
            headers=_auth_headers(),
        )
        started = client.post(
            "/ui/setup/start-auth",
            data={
                "api_id": str(api_id),
                "api_hash": api_hash,
                "phone_number": phone_number,
            },
            headers=_auth_headers(),
        )
        session_id = _extract_hidden_value(body=started.text, input_name="session_id")
        complete = client.post(
            "/ui/setup/verify-code",
            data={
                "session_id": session_id,
                "api_id": str(api_id),
                "api_hash": api_hash,
                "code": "12345",
            },
            headers=_auth_headers(),
        )
        exit_setup = client.get(
            "/ui/setup",
            headers=_auth_headers(),
            follow_redirects=False,
        )

    if complete.status_code != HTTPStatus.OK:
        raise AssertionError
    if "Setup Step 5: Session Saved" not in complete.text:
        raise AssertionError
    if _fetch_telegram_account_count(db_path=db_path) != 1:
        raise AssertionError
    if exit_setup.status_code != HTTPStatus.FOUND:
        raise AssertionError
    if exit_setup.headers.get("location") != "/ui":
        raise AssertionError


def _fetch_telegram_account_count(*, db_path: Path) -> int:
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute("SELECT COUNT(*) FROM telegram_accounts")
        row = cursor.fetchone()
    if row is None or not isinstance(row[0], int):
        raise AssertionError
    return row[0]


def _extract_hidden_value(*, body: str, input_name: str) -> str:
    escaped = re.escape(input_name)
    match = re.search(
        rf'name="{escaped}"\s+value="([^"]+)"',
        body,
    )
    if match is None:
        raise AssertionError
    return match.group(1)


def _token_side_effect() -> object:
    counter = itertools.count()

    def _next(*_args: object, **_kwargs: object) -> str:
        index = next(counter)
        if index == 0:
            return BOOTSTRAP_TOKEN
        return f"ui-setup-session-{index}"

    return _next


def _build_factory(
    *,
    mock_tg_client: MockTelegramClient,
    expected_api_id: int,
    expected_api_hash: str,
) -> object:
    """Build deterministic auth-client factory for setup wizard tests."""

    def _factory(
        api_id: int,
        api_hash: str,
        _session_string: str | None = None,
    ) -> MockTelegramClient:
        if api_id != expected_api_id:
            raise AssertionError
        if api_hash != expected_api_hash:
            raise AssertionError
        return mock_tg_client

    return _factory


def _configure_locked_auth_env(
    *,
    tmp_path: Path,
    monkeypatch: object,
    db_name: str,
    output_file_name: str,
) -> Path:
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
    return {"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"}


def _as_monkeypatch(value: object) -> MonkeyPatchLike:
    if not isinstance(value, MonkeyPatchLike):
        raise TypeError
    return value


@runtime_checkable
class MonkeyPatchLike(Protocol):
    """Runtime-checkable subset of pytest monkeypatch fixture behavior."""

    def setenv(self, name: str, value: str) -> None:
        """Set environment variable for duration of current test."""


class _FakeStringSession:
    """Minimal StringSession stand-in with deterministic save output."""

    def __init__(self, value: str) -> None:
        self._value = value

    def save(self) -> str:
        return self._value
