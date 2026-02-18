"""Tests for notifications UI list and acknowledgement interactions."""

from __future__ import annotations

import sqlite3
from http import HTTPStatus
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from unittest.mock import patch

from fastapi.testclient import TestClient

from tca.api.app import create_app

if TYPE_CHECKING:
    from pathlib import Path

BOOTSTRAP_TOKEN = "ui-notifications-bootstrap-token"  # noqa: S105


def test_notifications_view_lists_notifications_in_recency_order(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Notifications page should render newest notifications first."""
    db_path = _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="ui-notifications-list.sqlite3",
        output_file_name="ui-notifications-list-token.txt",
    )
    app = create_app()
    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        _insert_notification(
            db_path=db_path,
            notification_id=1,
            notification_type="auth_login_failed",
            severity="medium",
            message="oldest",
            created_at="2026-02-16T08:00:00+00:00",
        )
        _insert_notification(
            db_path=db_path,
            notification_id=2,
            notification_type="auth_login_failed",
            severity="medium",
            message="newest",
            created_at="2026-02-16T12:00:00+00:00",
        )
        _insert_notification(
            db_path=db_path,
            notification_id=3,
            notification_type="auth_registration_blocked",
            severity="high",
            message="middle",
            created_at="2026-02-16T10:00:00+00:00",
        )
        response = client.get("/ui/notifications", headers=_auth_headers())

    if response.status_code != HTTPStatus.OK:
        raise AssertionError
    _assert_substring_order(response.text, ("newest", "middle", "oldest"))


def test_notifications_view_acknowledge_updates_ui_and_db(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Acknowledge action should persist and render acknowledged state."""
    db_path = _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="ui-notifications-ack.sqlite3",
        output_file_name="ui-notifications-ack-token.txt",
    )
    app = create_app()
    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        _insert_notification(
            db_path=db_path,
            notification_id=101,
            notification_type="auth_login_failed",
            severity="medium",
            message="needs ack",
            created_at="2026-02-16T12:00:00+00:00",
        )
        response = client.post(
            "/ui/notifications/101/ack",
            headers=_auth_headers(),
            follow_redirects=True,
        )

    if response.status_code != HTTPStatus.OK:
        raise AssertionError
    if "Acknowledged at:" not in response.text:
        raise AssertionError
    if 'action="/ui/notifications/101/ack"' in response.text:
        raise AssertionError
    is_acknowledged, acknowledged_at = _fetch_ack_state(
        db_path=db_path,
        notification_id=101,
    )
    if is_acknowledged is not True:
        raise AssertionError
    if acknowledged_at is None:
        raise AssertionError


def test_notifications_view_acknowledge_is_idempotent(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Repeated acknowledge action should keep a stable acknowledged timestamp."""
    db_path = _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="ui-notifications-ack-idempotent.sqlite3",
        output_file_name="ui-notifications-ack-idempotent-token.txt",
    )
    app = create_app()
    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        _insert_notification(
            db_path=db_path,
            notification_id=102,
            notification_type="auth_login_failed",
            severity="medium",
            message="idempotent ack",
            created_at="2026-02-16T12:15:00+00:00",
        )
        first = client.post(
            "/ui/notifications/102/ack",
            headers=_auth_headers(),
            follow_redirects=True,
        )
        first_is_acknowledged, first_acknowledged_at = _fetch_ack_state(
            db_path=db_path,
            notification_id=102,
        )
        second = client.post(
            "/ui/notifications/102/ack",
            headers=_auth_headers(),
            follow_redirects=True,
        )
        second_is_acknowledged, second_acknowledged_at = _fetch_ack_state(
            db_path=db_path,
            notification_id=102,
        )

    if first.status_code != HTTPStatus.OK:
        raise AssertionError
    if second.status_code != HTTPStatus.OK:
        raise AssertionError
    if first_is_acknowledged is not True:
        raise AssertionError
    if second_is_acknowledged is not True:
        raise AssertionError
    if first_acknowledged_at is None:
        raise AssertionError
    if first_acknowledged_at != second_acknowledged_at:
        raise AssertionError
    if 'action="/ui/notifications/102/ack"' in second.text:
        raise AssertionError


def test_notifications_view_acknowledge_missing_notification_renders_not_found(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Missing notification id should return a 404 page-level error message."""
    _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="ui-notifications-ack-missing.sqlite3",
        output_file_name="ui-notifications-ack-missing-token.txt",
    )
    app = create_app()
    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            "/ui/notifications/9999/ack",
            headers=_auth_headers(),
        )

    if response.status_code != HTTPStatus.NOT_FOUND:
        raise AssertionError
    if "9999" not in response.text or "was not found." not in response.text:
        raise AssertionError


def test_notifications_view_requires_bearer_auth(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Notifications UI routes should reject unauthenticated requests."""
    _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="ui-notifications-auth.sqlite3",
        output_file_name="ui-notifications-auth-token.txt",
    )
    app = create_app()
    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        list_response = client.get("/ui/notifications")
        ack_response = client.post("/ui/notifications/42/ack")

    if list_response.status_code != HTTPStatus.UNAUTHORIZED:
        raise AssertionError
    if ack_response.status_code != HTTPStatus.UNAUTHORIZED:
        raise AssertionError


def test_notifications_view_marks_high_severity_alerts_visually(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """High-severity notifications should render with distinct visual styling."""
    db_path = _configure_auth_env(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        db_name="ui-notifications-high-severity.sqlite3",
        output_file_name="ui-notifications-high-severity-token.txt",
    )
    app = create_app()
    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        _insert_notification(
            db_path=db_path,
            notification_id=201,
            notification_type="auth_registration_blocked",
            severity="high",
            message="high alert",
            created_at="2026-02-16T12:00:00+00:00",
        )
        _insert_notification(
            db_path=db_path,
            notification_id=202,
            notification_type="auth_login_failed",
            severity="medium",
            message="medium alert",
            created_at="2026-02-16T11:00:00+00:00",
        )
        response = client.get("/ui/notifications", headers=_auth_headers())

    if response.status_code != HTTPStatus.OK:
        raise AssertionError
    if 'id="notification-201"' not in response.text:
        raise AssertionError
    if (
        'id="notification-201"\n      class="notification-card notification-high"'
        not in response.text
    ):
        raise AssertionError
    if 'id="notification-202"\n      class="notification-card"' not in response.text:
        raise AssertionError
    if "High-severity alert" not in response.text:
        raise AssertionError


def _insert_notification(  # noqa: PLR0913
    *,
    db_path: Path,
    notification_id: int,
    notification_type: str,
    severity: str,
    message: str,
    created_at: str,
    is_acknowledged: bool = False,
    acknowledged_at: str | None = None,
) -> None:
    with sqlite3.connect(db_path) as connection:
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
            VALUES (?, ?, ?, ?, NULL, ?, ?, ?)
            """,
            (
                notification_id,
                notification_type,
                severity,
                message,
                int(is_acknowledged),
                acknowledged_at,
                created_at,
            ),
        )
        connection.commit()


def _fetch_ack_state(*, db_path: Path, notification_id: int) -> tuple[bool, str | None]:
    with sqlite3.connect(db_path) as connection:
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
    return bool(row[0]), row[1]


def _assert_substring_order(body: str, ordered_values: tuple[str, ...]) -> None:
    positions = [body.find(value) for value in ordered_values]
    if -1 in positions:
        raise AssertionError
    if positions != sorted(positions):
        raise AssertionError


def _configure_auth_env(
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
