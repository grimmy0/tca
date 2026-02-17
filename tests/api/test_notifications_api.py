"""Tests for notifications API list and filtering behavior."""

from __future__ import annotations

import sqlite3
from http import HTTPStatus
from pathlib import Path
from typing import Protocol, cast, runtime_checkable
from unittest.mock import patch

from fastapi.testclient import TestClient

from tca.api.app import create_app

BOOTSTRAP_TOKEN = "notifications-api-token"  # noqa: S105
EXPECTED_OK_STATUS = HTTPStatus.OK
EXPECTED_UNAUTHORIZED_STATUS = HTTPStatus.UNAUTHORIZED


def test_list_notifications_returns_recent_first(
    tmp_path: object,
    monkeypatch: object,
) -> None:
    """Ensure list endpoint returns notifications in recency order."""
    db_path = _as_path(tmp_path) / "notifications-api-list.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (_as_path(tmp_path) / "notifications-bootstrap-token.txt").as_posix(),
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
            notification_id=1,
            notification_type="auth_registration_blocked",
            severity="high",
            message="alpha",
            created_at="2026-02-16T08:00:00+00:00",
        )
        _insert_notification(
            db_path,
            notification_id=2,
            notification_type="auth_login_failed",
            severity="medium",
            message="beta",
            created_at="2026-02-16T12:00:00+00:00",
        )
        _insert_notification(
            db_path,
            notification_id=3,
            notification_type="auth_login_failed",
            severity="medium",
            message="gamma",
            created_at="2026-02-16T10:00:00+00:00",
        )
        response = client.get("/notifications", headers=auth_headers)

    if response.status_code != EXPECTED_OK_STATUS:
        raise AssertionError

    payload = cast("list[dict[str, object]]", response.json())
    returned_ids = [item.get("id") for item in payload]
    if returned_ids != [2, 3, 1]:
        raise AssertionError


def test_list_notifications_filters_by_severity_and_type(
    tmp_path: object,
    monkeypatch: object,
) -> None:
    """Ensure list endpoint filters by severity and type."""
    db_path = _as_path(tmp_path) / "notifications-api-filter.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (_as_path(tmp_path) / "notifications-filter-token.txt").as_posix(),
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
            notification_id=1,
            notification_type="auth_registration_blocked",
            severity="high",
            message="alpha",
            created_at="2026-02-16T08:00:00+00:00",
        )
        _insert_notification(
            db_path,
            notification_id=2,
            notification_type="auth_login_failed",
            severity="medium",
            message="beta",
            created_at="2026-02-16T12:00:00+00:00",
        )
        _insert_notification(
            db_path,
            notification_id=3,
            notification_type="auth_login_failed",
            severity="high",
            message="gamma",
            created_at="2026-02-16T10:00:00+00:00",
        )
        response = client.get(
            "/notifications?severity=high&type=auth_login_failed",
            headers=auth_headers,
        )

    if response.status_code != EXPECTED_OK_STATUS:
        raise AssertionError

    payload = cast("list[dict[str, object]]", response.json())
    returned_ids = [item.get("id") for item in payload]
    if returned_ids != [3]:
        raise AssertionError


def test_list_notifications_requires_bearer_auth(
    tmp_path: object,
    monkeypatch: object,
) -> None:
    """Ensure list endpoint rejects unauthenticated requests."""
    db_path = _as_path(tmp_path) / "notifications-api-auth.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (_as_path(tmp_path) / "notifications-auth-token.txt").as_posix(),
    )

    app = create_app()
    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        response = client.get("/notifications")

    if response.status_code != EXPECTED_UNAUTHORIZED_STATUS:
        raise AssertionError


def _insert_notification(
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


def _auth_headers() -> dict[str, str]:
    """Build Authorization header for tests."""
    return {"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"}


def _as_path(value: object) -> "Path":
    """Narrow input to Path for temp dir handling."""
    if not isinstance(value, Path):
        raise TypeError
    return value


def _as_monkeypatch(value: object) -> "MonkeyPatchLike":
    """Narrow monkeypatch fixture object to setenv-capable helper."""
    if not isinstance(value, MonkeyPatchLike):
        raise TypeError
    return value


@runtime_checkable
class MonkeyPatchLike(Protocol):
    """Runtime-checkable subset of pytest monkeypatch fixture behavior."""

    def setenv(self, name: str, value: str) -> None:
        """Set environment variable for duration of current test."""
