"""Tests for notification acknowledge API behavior."""

from __future__ import annotations

import sqlite3
from http import HTTPStatus
from pathlib import Path
from typing import Protocol, cast, runtime_checkable
from unittest.mock import patch

from fastapi.testclient import TestClient

from tca.api.app import create_app

BOOTSTRAP_TOKEN = "notifications-ack-token"  # noqa: S105
EXPECTED_OK_STATUS = HTTPStatus.OK


def test_acknowledge_notification_updates_state_atomically(
    tmp_path: object,
    monkeypatch: object,
) -> None:
    """Ensure acknowledge updates persisted state and returns updated payload."""
    db_path = _as_path(tmp_path) / "notifications-ack-update.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (_as_path(tmp_path) / "notifications-ack-token.txt").as_posix(),
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
        _insert_notification(
            db_path,
            notification_id=101,
            notification_type="auth_login_failed",
            severity="medium",
            message="alpha",
            created_at="2026-02-16T08:00:00+00:00",
        )
        response = client.put("/notifications/101/ack", headers=auth_headers)

    if response.status_code != EXPECTED_OK_STATUS:
        raise AssertionError

    payload = cast("dict[str, object]", response.json())
    if payload.get("id") != 101:  # noqa: PLR2004
        raise AssertionError
    if payload.get("is_acknowledged") is not True:
        raise AssertionError
    if payload.get("acknowledged_at") is None:
        raise AssertionError

    is_acknowledged, acknowledged_at = _fetch_ack_state(
        db_path=db_path,
        notification_id=101,
    )
    if is_acknowledged is not True:
        raise AssertionError
    if acknowledged_at is None:
        raise AssertionError


def test_acknowledge_notification_is_idempotent(
    tmp_path: object,
    monkeypatch: object,
) -> None:
    """Ensure repeated acknowledge calls return the same state."""
    db_path = _as_path(tmp_path) / "notifications-ack-idempotent.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (_as_path(tmp_path) / "notifications-ack-token-idem.txt").as_posix(),
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
        _insert_notification(
            db_path,
            notification_id=202,
            notification_type="auth_registration_blocked",
            severity="high",
            message="beta",
            created_at="2026-02-16T08:15:00+00:00",
        )
        response_one = client.put("/notifications/202/ack", headers=auth_headers)
        response_two = client.put("/notifications/202/ack", headers=auth_headers)

    if response_one.status_code != EXPECTED_OK_STATUS:
        raise AssertionError
    if response_two.status_code != EXPECTED_OK_STATUS:
        raise AssertionError

    payload_one = cast("dict[str, object]", response_one.json())
    payload_two = cast("dict[str, object]", response_two.json())
    if payload_one.get("is_acknowledged") is not True:
        raise AssertionError
    if payload_two.get("is_acknowledged") is not True:
        raise AssertionError
    if payload_one.get("acknowledged_at") is None:
        raise AssertionError
    if payload_one.get("acknowledged_at") != payload_two.get("acknowledged_at"):
        raise AssertionError


def _insert_notification(  # noqa: PLR0913
    db_path: object,
    *,
    notification_id: int,
    notification_type: str,
    severity: str,
    message: str,
    created_at: str,
    payload_json: str | None = None,
    is_acknowledged: bool = False,
    acknowledged_at: str | None = None,
) -> None:
    """Insert notification row into sqlite storage."""
    with sqlite3.connect(_as_path(db_path)) as connection:
        connection.execute(
            """
            INSERT INTO notifications (
                id,
                type,
                severity,
                message,
                payload_json,
                is_acknowledged,
                acknowledged_at,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                notification_id,
                notification_type,
                severity,
                message,
                payload_json,
                int(is_acknowledged),
                acknowledged_at,
                created_at,
            ),
        )
        connection.commit()


def _fetch_ack_state(
    *,
    db_path: object,
    notification_id: int,
) -> tuple[bool, str | None]:
    """Fetch acknowledgement state from sqlite storage."""
    with sqlite3.connect(_as_path(db_path)) as connection:
        row = connection.execute(
            """
            SELECT is_acknowledged, acknowledged_at
            FROM notifications
            WHERE id = ?
            """,
            (notification_id,),
        ).fetchone()
    if row is None:
        raise AssertionError
    return bool(row[0]), cast("str | None", row[1])


def _auth_headers() -> dict[str, str]:
    """Build Authorization header for tests."""
    return {"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"}


def _as_path(value: object) -> Path:
    """Narrow input to Path for temp dir handling."""
    if not isinstance(value, Path):
        raise TypeError
    return value


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
