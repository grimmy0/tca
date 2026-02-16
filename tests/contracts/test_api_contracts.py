"""API contract tests that gate schema stability and route behavior."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable
from unittest.mock import patch

from fastapi.testclient import TestClient

from tca.api.app import create_app

if TYPE_CHECKING:
    from pathlib import Path

OPENAPI_BEARER_TOKEN = "api-contract-openapi-token"  # noqa: S105


def test_health_openapi_contract_uses_component_schema(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure /health response links to explicit HealthResponse schema."""
    db_path = tmp_path / "api-contract-openapi.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (tmp_path / "api-contract-openapi-token.txt").as_posix(),
    )

    app = create_app()
    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=OPENAPI_BEARER_TOKEN,
        ),
        TestClient(app) as client,
    ):
        response = client.get(
            "/openapi.json",
            headers={"Authorization": f"Bearer {OPENAPI_BEARER_TOKEN}"},
        )
    if response.status_code != HTTPStatus.OK:
        raise AssertionError
    openapi = cast("dict[str, object]", response.json())

    paths = cast("dict[str, object]", openapi["paths"])
    health_path = cast("dict[str, object]", paths["/health"])
    get_operation = cast("dict[str, object]", health_path["get"])
    responses = cast("dict[str, object]", get_operation["responses"])
    ok_response = cast("dict[str, object]", responses["200"])
    content = cast("dict[str, object]", ok_response["content"])
    app_json = cast("dict[str, object]", content["application/json"])
    schema = cast("dict[str, object]", app_json["schema"])

    if schema.get("$ref") != "#/components/schemas/HealthResponse":
        raise AssertionError


def test_health_component_schema_has_required_properties(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure HealthResponse schema is explicit and non-generic."""
    db_path = tmp_path / "api-contract-component.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (tmp_path / "api-contract-component-token.txt").as_posix(),
    )

    app = create_app()
    with (
        patch(
            "tca.auth.bootstrap_token.secrets.token_urlsafe",
            return_value=OPENAPI_BEARER_TOKEN,
        ),
        TestClient(app) as client,
    ):
        response = client.get(
            "/openapi.json",
            headers={"Authorization": f"Bearer {OPENAPI_BEARER_TOKEN}"},
        )
    if response.status_code != HTTPStatus.OK:
        raise AssertionError
    openapi = cast("dict[str, object]", response.json())

    components = cast("dict[str, object]", openapi["components"])
    schemas = cast("dict[str, object]", components["schemas"])
    health_schema = cast("dict[str, object]", schemas["HealthResponse"])
    properties = cast("dict[str, object]", health_schema["properties"])

    if health_schema.get("required") != ["status", "timestamp"]:
        raise AssertionError
    if health_schema.get("type") != "object":
        raise AssertionError

    status_schema = cast("dict[str, object]", properties["status"])
    status_enum = status_schema.get("enum")
    status_const = status_schema.get("const")
    if status_enum != ["ok"] and status_const != "ok":
        raise AssertionError


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
