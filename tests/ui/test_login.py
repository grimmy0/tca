"""Integration tests for browser session login and logout flow."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from unittest.mock import patch

from fastapi.testclient import TestClient

from tca.api.app import create_app
from tca.api.cookie_auth import SESSION_COOKIE_NAME

if TYPE_CHECKING:
    from pathlib import Path

BOOTSTRAP_TOKEN = "login-test-bootstrap-token"  # noqa: S105
INVALID_TOKEN = "login-test-invalid-token"  # noqa: S105


def test_login_page_renders_without_auth(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """GET /ui/login should return 200 without any authentication."""
    _configure_auth_env(tmp_path=tmp_path, monkeypatch=monkeypatch)
    app = create_app()

    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        response = client.get("/ui/login")

    if response.status_code != HTTPStatus.OK:
        raise AssertionError
    if "Bootstrap Token" not in response.text:
        raise AssertionError


def test_login_valid_token_sets_cookie_and_redirects(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """POST /ui/login with valid token should set session cookie and redirect."""
    _configure_auth_env(tmp_path=tmp_path, monkeypatch=monkeypatch)
    app = create_app()

    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app, follow_redirects=False) as client,
    ):
        response = client.post(
            "/ui/login",
            data={"token": BOOTSTRAP_TOKEN},
        )

    if response.status_code != HTTPStatus.FOUND:
        raise AssertionError
    if response.headers.get("location") != "/ui":
        raise AssertionError
    set_cookie = response.headers.get("set-cookie", "")
    if SESSION_COOKIE_NAME not in set_cookie:
        raise AssertionError


def test_login_invalid_token_shows_error(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """POST /ui/login with wrong token should show error message."""
    _configure_auth_env(tmp_path=tmp_path, monkeypatch=monkeypatch)
    app = create_app()

    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        response = client.post(
            "/ui/login",
            data={"token": INVALID_TOKEN},
        )

    if response.status_code != HTTPStatus.OK:
        raise AssertionError
    if "Invalid token" not in response.text:
        raise AssertionError


def test_ui_with_valid_cookie_returns_200(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """GET /ui with a valid session cookie should return 200."""
    _configure_auth_env(tmp_path=tmp_path, monkeypatch=monkeypatch)
    app = create_app()

    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        client.post(
            "/ui/login",
            data={"token": BOOTSTRAP_TOKEN},
            follow_redirects=False,
        )
        response = client.get("/ui")

    if response.status_code != HTTPStatus.OK:
        raise AssertionError
    if "Minimal UI shell is ready." not in response.text:
        raise AssertionError


def test_ui_without_auth_redirects_to_login(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """GET /ui without auth should redirect to /ui/login."""
    _configure_auth_env(tmp_path=tmp_path, monkeypatch=monkeypatch)
    app = create_app()

    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app, follow_redirects=False) as client,
    ):
        response = client.get("/ui")

    if response.status_code != HTTPStatus.FOUND:
        raise AssertionError
    if response.headers.get("location") != "/ui/login":
        raise AssertionError


def test_logout_clears_cookie(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """POST /ui/logout should clear session cookie and redirect to login."""
    _configure_auth_env(tmp_path=tmp_path, monkeypatch=monkeypatch)
    app = create_app()

    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app, follow_redirects=False) as client,
    ):
        # Login first
        client.post("/ui/login", data={"token": BOOTSTRAP_TOKEN})
        # Then logout
        response = client.post("/ui/logout")

    if response.status_code != HTTPStatus.FOUND:
        raise AssertionError
    if response.headers.get("location") != "/ui/login":
        raise AssertionError
    set_cookie = response.headers.get("set-cookie", "")
    if SESSION_COOKIE_NAME not in set_cookie:
        raise AssertionError


def _configure_auth_env(*, tmp_path: Path, monkeypatch: object) -> None:
    """Set per-test DB and bootstrap token output paths."""
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", (tmp_path / "login-test.sqlite3").as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (tmp_path / "login-test-bootstrap-token.txt").as_posix(),
    )


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
