"""Tests for bearer auth protection on non-health API routes."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from unittest.mock import patch

from fastapi.testclient import TestClient

from tca.api.app import create_app

if TYPE_CHECKING:
    from pathlib import Path

PROTECTED_ROUTE_PATH = "/settings/scheduler.max_pages_per_poll"
BOOTSTRAP_TOKEN = "valid-bootstrap-token"  # noqa: S105
INVALID_BEARER_TOKEN = "invalid-bearer-token"  # noqa: S105


def test_unauthenticated_protected_route_returns_401(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure protected route rejects requests without Authorization header."""
    _configure_auth_env(tmp_path=tmp_path, monkeypatch=monkeypatch)
    app = create_app()

    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        response = client.get(PROTECTED_ROUTE_PATH)

    if response.status_code != HTTPStatus.UNAUTHORIZED:
        raise AssertionError


def test_invalid_token_returns_401(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure protected route rejects invalid bearer token values."""
    _configure_auth_env(tmp_path=tmp_path, monkeypatch=monkeypatch)
    app = create_app()

    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        response = client.get(
            PROTECTED_ROUTE_PATH,
            headers={"Authorization": f"Bearer {INVALID_BEARER_TOKEN}"},
        )

    if response.status_code != HTTPStatus.UNAUTHORIZED:
        raise AssertionError


def test_valid_token_returns_200_for_protected_route(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure protected route allows request when bearer token is valid."""
    _configure_auth_env(tmp_path=tmp_path, monkeypatch=monkeypatch)
    app = create_app()

    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        response = client.get(
            PROTECTED_ROUTE_PATH,
            headers={"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"},
        )

    if response.status_code != HTTPStatus.OK:
        raise AssertionError


def _configure_auth_env(*, tmp_path: Path, monkeypatch: object) -> None:
    """Set per-test DB and bootstrap token output paths."""
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", (tmp_path / "bearer-auth.sqlite3").as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (tmp_path / "bootstrap-bearer-token.txt").as_posix(),
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
