"""Tests for minimal authenticated UI shell."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from unittest.mock import patch

from fastapi.testclient import TestClient

from tca.api.app import create_app

if TYPE_CHECKING:
    from pathlib import Path

BOOTSTRAP_TOKEN = "ui-shell-bootstrap-token"  # noqa: S105
INVALID_BEARER_TOKEN = "ui-shell-invalid-token"  # noqa: S105


def test_ui_shell_renders_without_node_build_pipeline(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure the shell route returns rendered HTML without bundled JS artifacts."""
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
            "/ui",
            headers={"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"},
        )

    if response.status_code != HTTPStatus.OK:
        raise AssertionError
    text = response.text
    if "Minimal UI shell is ready." not in text:
        raise AssertionError
    if "bundle.js" in text:
        raise AssertionError


def test_ui_shell_base_template_loads_htmx_and_css(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure base template includes HTMX and stylesheet links."""
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
            "/ui",
            headers={"Authorization": f"Bearer {BOOTSTRAP_TOKEN}"},
        )
        css_response = client.get("/ui/static/styles/shell.css")

    if response.status_code != HTTPStatus.OK:
        raise AssertionError
    body = response.text
    for fragment in (
        "@picocss/pico",
        "unpkg.com/htmx.org",
        "/ui/static/styles/shell.css",
    ):
        if fragment not in body:
            raise AssertionError

    if css_response.status_code != HTTPStatus.OK:
        raise AssertionError
    if "--tca-shell-max-width" not in css_response.text:
        raise AssertionError


def test_ui_shell_rejects_unauthorized_access(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure UI shell route is protected by bearer auth."""
    _configure_auth_env(tmp_path=tmp_path, monkeypatch=monkeypatch)
    app = create_app()

    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=BOOTSTRAP_TOKEN,
        ),
        TestClient(app) as client,
    ):
        unauthenticated = client.get("/ui")
        invalid = client.get(
            "/ui",
            headers={"Authorization": f"Bearer {INVALID_BEARER_TOKEN}"},
        )

    if unauthenticated.status_code != HTTPStatus.UNAUTHORIZED:
        raise AssertionError
    if invalid.status_code != HTTPStatus.UNAUTHORIZED:
        raise AssertionError


def _configure_auth_env(*, tmp_path: Path, monkeypatch: object) -> None:
    """Set per-test DB and bootstrap token output paths."""
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", (tmp_path / "ui-shell.sqlite3").as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (tmp_path / "ui-shell-bootstrap-token.txt").as_posix(),
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
