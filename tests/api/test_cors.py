"""Tests for API CORS allowlist behavior."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from fastapi.testclient import TestClient

from tca.api.app import create_app

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

HEALTH_ROUTE_PATH = "/health"
ALLOWED_ORIGIN = "https://ui.allowed.example"
BLOCKED_ORIGIN = "https://ui.blocked.example"


def test_origin_not_allowlisted_receives_no_cors_headers(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure non-allowlisted origins do not receive CORS response headers."""
    _configure_cors_env(tmp_path=tmp_path, monkeypatch=monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        response = client.get(
            HEALTH_ROUTE_PATH,
            headers={"Origin": BLOCKED_ORIGIN},
        )

    if response.status_code != HTTPStatus.OK:
        raise AssertionError

    headers = cast("Mapping[str, str]", response.headers)
    cors_headers = [
        header for header in headers if header.lower().startswith("access-control-")
    ]
    if cors_headers:
        raise AssertionError


def test_allowlisted_origin_receives_expected_cors_headers(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure allowlisted origins receive expected CORS response headers."""
    _configure_cors_env(tmp_path=tmp_path, monkeypatch=monkeypatch)
    app = create_app()

    with TestClient(app) as client:
        response = client.get(
            HEALTH_ROUTE_PATH,
            headers={"Origin": ALLOWED_ORIGIN},
        )

    if response.status_code != HTTPStatus.OK:
        raise AssertionError
    headers = cast("Mapping[str, str]", response.headers)
    if headers.get("access-control-allow-origin") != ALLOWED_ORIGIN:
        raise AssertionError

    vary = headers.get("vary")
    if vary is None or "origin" not in vary.lower():
        raise AssertionError


def _configure_cors_env(*, tmp_path: Path, monkeypatch: object) -> None:
    """Set per-test env vars for DB, bootstrap token output, and CORS allowlist."""
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", (tmp_path / "cors-api.sqlite3").as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (tmp_path / "cors-bootstrap-token.txt").as_posix(),
    )
    patcher.setenv("TCA_CORS_ALLOW_ORIGINS", ALLOWED_ORIGIN)


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
