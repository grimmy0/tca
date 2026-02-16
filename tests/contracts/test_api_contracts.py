"""API contract tests that gate schema stability and route behavior."""

from __future__ import annotations

from typing import cast

from fastapi.testclient import TestClient

from tca.api.app import create_app


def test_health_openapi_contract_uses_component_schema() -> None:
    """Ensure /health response links to explicit HealthResponse schema."""
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


def test_health_component_schema_has_required_properties() -> None:
    """Ensure HealthResponse schema is explicit and non-generic."""
    app = create_app()
    with TestClient(app) as client:
        openapi = cast("dict[str, object]", client.get("/openapi.json").json())

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
