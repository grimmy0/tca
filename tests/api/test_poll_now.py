"""Tests for the manual poll-now job trigger endpoint."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from typing import Protocol, cast, runtime_checkable
from unittest.mock import patch

from fastapi.testclient import TestClient

from tca.api.app import create_app

BOOTSTRAP_TOKEN = "poll-now-token"  # noqa: S105
DEFAULT_ACCOUNT_ID = 1
DEFAULT_CHANNEL_ID = 1
DEFAULT_TELEGRAM_CHANNEL_ID = 22001
EXPECTED_ACCEPTED_STATUS = HTTPStatus.ACCEPTED
EXPECTED_CONFLICT_STATUS = HTTPStatus.CONFLICT


def test_poll_now_enqueues_job_for_active_channel(
    tmp_path: object,
    monkeypatch: object,
) -> None:
    """Ensure active channels enqueue poll jobs and return correlation IDs."""
    db_path = _as_path(tmp_path) / "poll-now-active.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (_as_path(tmp_path) / "poll-now-bootstrap-token.txt").as_posix(),
    )

    app = create_app()
    auth_headers = _auth_headers()
    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        _insert_account_fixture(db_path, account_id=DEFAULT_ACCOUNT_ID)
        _insert_channel_fixture(
            db_path,
            channel_id=DEFAULT_CHANNEL_ID,
            account_id=DEFAULT_ACCOUNT_ID,
            telegram_channel_id=DEFAULT_TELEGRAM_CHANNEL_ID,
            name="alpha",
            is_enabled=True,
        )
        response = client.post(
            f"/jobs/poll-now/{DEFAULT_CHANNEL_ID}",
            headers=auth_headers,
        )

    if response.status_code != EXPECTED_ACCEPTED_STATUS:
        raise AssertionError

    payload = cast("dict[str, object]", response.json())
    correlation_id = payload.get("correlation_id")
    if payload.get("channel_id") != DEFAULT_CHANNEL_ID:
        raise AssertionError
    if not isinstance(correlation_id, str) or not correlation_id:
        raise AssertionError

    job = _read_poll_job(db_path, channel_id=DEFAULT_CHANNEL_ID)
    if job is None:
        raise AssertionError
    if job != correlation_id:
        raise AssertionError


def test_poll_now_rejects_disabled_channel(
    tmp_path: object,
    monkeypatch: object,
) -> None:
    """Ensure disabled channels return a deterministic rejection."""
    db_path = _as_path(tmp_path) / "poll-now-disabled.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (_as_path(tmp_path) / "poll-now-disabled-bootstrap-token.txt").as_posix(),
    )

    app = create_app()
    auth_headers = _auth_headers()
    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        _insert_account_fixture(db_path, account_id=DEFAULT_ACCOUNT_ID)
        _insert_channel_fixture(
            db_path,
            channel_id=DEFAULT_CHANNEL_ID,
            account_id=DEFAULT_ACCOUNT_ID,
            telegram_channel_id=DEFAULT_TELEGRAM_CHANNEL_ID,
            name="alpha",
            is_enabled=False,
        )
        response = client.post(
            f"/jobs/poll-now/{DEFAULT_CHANNEL_ID}",
            headers=auth_headers,
        )

    if response.status_code != EXPECTED_CONFLICT_STATUS:
        raise AssertionError
    if response.json().get("detail") != f"Channel '{DEFAULT_CHANNEL_ID}' is disabled.":
        raise AssertionError

    if _read_poll_job(db_path, channel_id=DEFAULT_CHANNEL_ID) is not None:
        raise AssertionError


def test_poll_now_rejects_paused_channel(
    tmp_path: object,
    monkeypatch: object,
) -> None:
    """Ensure paused channels return a deterministic rejection."""
    db_path = _as_path(tmp_path) / "poll-now-paused.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (_as_path(tmp_path) / "poll-now-paused-bootstrap-token.txt").as_posix(),
    )

    paused_until = datetime.now(timezone.utc) + timedelta(minutes=30)

    app = create_app()
    auth_headers = _auth_headers()
    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        _insert_account_fixture(db_path, account_id=DEFAULT_ACCOUNT_ID)
        _insert_channel_fixture(
            db_path,
            channel_id=DEFAULT_CHANNEL_ID,
            account_id=DEFAULT_ACCOUNT_ID,
            telegram_channel_id=DEFAULT_TELEGRAM_CHANNEL_ID,
            name="alpha",
            is_enabled=True,
        )
        _insert_channel_state_fixture(
            db_path,
            channel_id=DEFAULT_CHANNEL_ID,
            paused_until=paused_until,
        )
        response = client.post(
            f"/jobs/poll-now/{DEFAULT_CHANNEL_ID}",
            headers=auth_headers,
        )

    if response.status_code != EXPECTED_CONFLICT_STATUS:
        raise AssertionError
    expected_detail = (
        f"Channel '{DEFAULT_CHANNEL_ID}' is paused until {paused_until.isoformat()}."
    )
    if response.json().get("detail") != expected_detail:
        raise AssertionError

    if _read_poll_job(db_path, channel_id=DEFAULT_CHANNEL_ID) is not None:
        raise AssertionError


def _insert_account_fixture(db_path: object, *, account_id: int) -> None:
    """Insert a Telegram account row fixture."""
    with sqlite3.connect(_as_path(db_path).as_posix()) as connection:
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _ = connection.execute(
            """
            INSERT INTO telegram_accounts (id, api_id, api_hash_encrypted)
            VALUES (?, ?, ?)
            """,
            (account_id, 12345, b"encrypted-api-hash"),
        )
        connection.commit()


def _insert_channel_fixture(
    db_path: object,
    *,
    channel_id: int,
    account_id: int,
    telegram_channel_id: int,
    name: str,
    is_enabled: bool,
) -> None:
    """Insert a Telegram channel row fixture."""
    with sqlite3.connect(_as_path(db_path).as_posix()) as connection:
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _ = connection.execute(
            """
            INSERT INTO telegram_channels (
                id,
                account_id,
                telegram_channel_id,
                name,
                is_enabled
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (channel_id, account_id, telegram_channel_id, name, int(is_enabled)),
        )
        connection.commit()


def _insert_channel_state_fixture(
    db_path: object,
    *,
    channel_id: int,
    paused_until: datetime,
) -> None:
    """Insert a channel state row fixture."""
    with sqlite3.connect(_as_path(db_path).as_posix()) as connection:
        _ = connection.execute("PRAGMA foreign_keys = ON")
        _ = connection.execute(
            """
            INSERT INTO channel_state (channel_id, paused_until, last_success_at)
            VALUES (?, ?, ?)
            """,
            (channel_id, paused_until.isoformat(), None),
        )
        connection.commit()


def _read_poll_job(db_path: object, *, channel_id: int) -> str | None:
    """Read correlation id for the queued poll job."""
    with sqlite3.connect(_as_path(db_path).as_posix()) as connection:
        cursor = connection.execute(
            """
            SELECT correlation_id
            FROM poll_jobs
            WHERE channel_id = ?
            """,
            (channel_id,),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    return cast("str", row[0])


def _auth_headers() -> dict[str, str]:
    """Build auth headers with the bootstrap bearer token."""
    return {"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"}


@runtime_checkable
class _MonkeyPatch(Protocol):
    def setenv(self, name: str, value: str) -> None:
        """Proxy for monkeypatch.setenv."""


@runtime_checkable
class _PathLike(Protocol):
    def __truediv__(self, name: str) -> "_PathLike":
        """Join path fragments with /."""

    def as_posix(self) -> str:
        """Return POSIX path string."""


def _as_path(path: object) -> _PathLike:
    """Cast pytest tmp_path to protocol for type checking."""
    return cast("_PathLike", path)


def _as_monkeypatch(patcher: object) -> _MonkeyPatch:
    """Cast pytest monkeypatch to protocol for type checking."""
    return cast("_MonkeyPatch", patcher)
