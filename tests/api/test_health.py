"""Tests for the /health endpoint."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from fastapi.testclient import TestClient

from tca.api.app import create_app

if TYPE_CHECKING:
    from pathlib import Path


def test_get_health_returns_ok(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure GET /health returns 200 and deterministic schema."""
    db_path = tmp_path / "health-api.sqlite3"
    _as_monkeypatch(monkeypatch).setenv("TCA_DB_PATH", db_path.as_posix())

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/health")

    if response.status_code != HTTPStatus.OK:
        raise AssertionError
    data = cast("dict[str, object]", response.json())
    if data["status"] != "ok":
        raise AssertionError
    if "timestamp" not in data:
        raise AssertionError


def test_health_openapi_schema_is_explicit_and_stable(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure /health response schema is explicit in OpenAPI components."""
    db_path = tmp_path / "health-openapi.sqlite3"
    _as_monkeypatch(monkeypatch).setenv("TCA_DB_PATH", db_path.as_posix())

    app = create_app()
    with TestClient(app) as client:
        openapi = cast("dict[str, object]", client.get("/openapi.json").json())

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

    components = cast("dict[str, object]", openapi["components"])
    schemas = cast("dict[str, object]", components["schemas"])
    health_schema = cast("dict[str, object]", schemas["HealthResponse"])
    properties = cast("dict[str, object]", health_schema["properties"])

    if health_schema.get("required") != ["status", "timestamp"]:
        raise AssertionError
    status_schema = cast("dict[str, object]", properties["status"])
    if status_schema.get("const") != "ok" and status_schema.get("enum") != ["ok"]:
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
