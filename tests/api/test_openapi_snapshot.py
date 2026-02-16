"""OpenAPI contract snapshot tests for settings and channel-groups routes."""

from __future__ import annotations

import difflib
import json
import os
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable
from unittest.mock import patch

from fastapi.testclient import TestClient

from tca.api.app import create_app

if TYPE_CHECKING:
    from collections.abc import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = (
    PROJECT_ROOT / "tests" / "api" / "snapshots" / "config_groups_openapi_snapshot.json"
)
UPDATE_ENV_VAR = "TCA_UPDATE_OPENAPI_SNAPSHOT"
COMPONENT_SCHEMA_PREFIX = "#/components/schemas/"
OPENAPI_BEARER_TOKEN = "openapi-schema-token"  # noqa: S105
TARGET_PATHS = (
    "/channel-groups",
    "/channel-groups/{group_id}",
    "/channel-groups/{group_id}/channels/{channel_id}",
    "/settings/{key}",
)
REQUIRED_SCHEMA_FIELDS = {
    "SettingUpsertRequest": {"value"},
    "SettingUpsertResponse": {"key", "value"},
    "ChannelGroupCreateRequest": {
        "name",
        "description",
        "dedupe_horizon_minutes_override",
    },
    "ChannelGroupPatchRequest": {
        "name",
        "description",
        "dedupe_horizon_minutes_override",
    },
    "ChannelGroupResponse": {
        "id",
        "name",
        "description",
        "dedupe_horizon_minutes_override",
    },
    "ChannelGroupDeleteResponse": {"deleted_group_id"},
    "ChannelGroupMembershipResponse": {"group_id", "channel_id", "is_member"},
}


def test_openapi_snapshot_includes_config_and_group_endpoints_and_fields(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure snapshot coverage includes all config/groups paths and payload fields."""
    openapi = _load_openapi(tmp_path=tmp_path, monkeypatch=monkeypatch)
    snapshot = _build_config_groups_snapshot(openapi=openapi)
    path_map = _expect_dict(snapshot.get("paths"))
    if set(path_map) != set(TARGET_PATHS):
        raise AssertionError

    components = _expect_dict(snapshot.get("components"))
    schemas = _expect_dict(components.get("schemas"))
    for schema_name, expected_fields in REQUIRED_SCHEMA_FIELDS.items():
        schema = _expect_dict(schemas.get(schema_name))
        properties = _expect_dict(schema.get("properties"))
        if not expected_fields.issubset(set(properties)):
            raise AssertionError


def test_openapi_snapshot_matches_committed_contract(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure contract drift fails until the reviewed snapshot file is updated."""
    openapi = _load_openapi(tmp_path=tmp_path, monkeypatch=monkeypatch)
    actual = _build_config_groups_snapshot(openapi=openapi)

    if os.getenv(UPDATE_ENV_VAR) == "1":
        _write_snapshot(actual)

    expected = _read_snapshot()
    if actual != expected:
        diff = _build_snapshot_diff(expected=expected, actual=actual)
        message = (
            "OpenAPI contract drift detected for settings/channel-groups.\n"
            f"Run `{UPDATE_ENV_VAR}=1 uv run pytest -q "
            "tests/api/test_openapi_snapshot.py` after reviewing the contract diff.\n"
            f"{diff}"
        )
        raise AssertionError(message)


def _load_openapi(*, tmp_path: Path, monkeypatch: object) -> dict[str, object]:
    """Load OpenAPI payload from an isolated per-test SQLite file."""
    db_path = tmp_path / "openapi-config-groups.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (tmp_path / "openapi-bootstrap-token.txt").as_posix(),
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
    payload = cast("object", response.json())
    return _expect_dict(payload)


def _build_config_groups_snapshot(*, openapi: dict[str, object]) -> dict[str, object]:
    """Build deterministic OpenAPI subset for config/group endpoint contracts."""
    paths = _expect_dict(openapi.get("paths"))
    selected_paths: dict[str, object] = {}
    for route_path in TARGET_PATHS:
        selected_paths[route_path] = _expect_dict(paths.get(route_path))

    components = _expect_dict(openapi.get("components"))
    schemas = _expect_dict(components.get("schemas"))
    direct_schema_names = _collect_schema_names(selected_paths.values())
    schema_names = _expand_schema_names(
        direct_schema_names=direct_schema_names,
        schemas=schemas,
    )
    selected_schemas: dict[str, object] = {}
    for schema_name in sorted(schema_names):
        selected_schemas[schema_name] = _expect_dict(schemas.get(schema_name))

    path_snapshot = {
        route_path: selected_paths[route_path] for route_path in TARGET_PATHS
    }
    return {
        "paths": path_snapshot,
        "components": {"schemas": selected_schemas},
    }


def _collect_schema_names(nodes: Iterable[object]) -> set[str]:
    """Collect direct schema names referenced by selected OpenAPI path entries."""
    schema_names: set[str] = set()
    for node in nodes:
        for reference in _iter_schema_references(node):
            parsed = _parse_schema_name(reference)
            if parsed is not None:
                schema_names.add(parsed)
    return schema_names


def _expand_schema_names(
    *,
    direct_schema_names: set[str],
    schemas: dict[str, object],
) -> set[str]:
    """Expand schema set transitively for nested refs like ValidationError."""
    expanded: set[str] = set()
    pending = list(direct_schema_names)
    while pending:
        schema_name = pending.pop()
        if schema_name in expanded:
            continue
        expanded.add(schema_name)
        schema = _expect_dict(schemas.get(schema_name))
        for reference in _iter_schema_references(schema):
            parsed = _parse_schema_name(reference)
            if parsed is not None and parsed not in expanded:
                pending.append(parsed)
    return expanded


def _iter_schema_references(node: object) -> list[str]:
    """Recursively collect schema refs from OpenAPI structures."""
    references: list[str] = []
    if isinstance(node, dict):
        entries = cast("dict[object, object]", node)
        for key, value in entries.items():
            if key == "$ref" and isinstance(value, str):
                references.append(value)
                continue
            references.extend(_iter_schema_references(value))
        return references
    if isinstance(node, list):
        list_entries = cast("list[object]", node)
        for value in list_entries:
            references.extend(_iter_schema_references(value))
    return references


def _parse_schema_name(reference: str) -> str | None:
    """Parse OpenAPI component schema reference into schema name."""
    if not reference.startswith(COMPONENT_SCHEMA_PREFIX):
        return None
    return reference.removeprefix(COMPONENT_SCHEMA_PREFIX)


def _read_snapshot() -> dict[str, object]:
    """Read committed OpenAPI snapshot from disk."""
    if not SNAPSHOT_PATH.exists():
        message = (
            f"Missing snapshot file: {SNAPSHOT_PATH}. "
            f"Run `{UPDATE_ENV_VAR}=1 uv run pytest -q "
            "tests/api/test_openapi_snapshot.py`."
        )
        raise AssertionError(message)
    payload = cast(
        "object",
        json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8")),
    )
    return _expect_dict(payload)


def _write_snapshot(snapshot: dict[str, object]) -> None:
    """Write deterministic OpenAPI snapshot JSON for reviewed contract updates."""
    SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    _ = SNAPSHOT_PATH.write_text(_to_stable_json(snapshot), encoding="utf-8")


def _build_snapshot_diff(
    *,
    expected: dict[str, object],
    actual: dict[str, object],
) -> str:
    """Render unified diff for easier review of contract drift."""
    expected_lines = _to_stable_json(expected).splitlines(keepends=True)
    actual_lines = _to_stable_json(actual).splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            expected_lines,
            actual_lines,
            fromfile=SNAPSHOT_PATH.as_posix(),
            tofile="generated-openapi",
        ),
    )


def _to_stable_json(payload: dict[str, object]) -> str:
    """Serialize snapshot payload with deterministic key ordering."""
    return f"{json.dumps(payload, indent=2, sort_keys=True)}\n"


def _expect_dict(value: object) -> dict[str, object]:
    """Narrow object to string-keyed dictionary for strict typing."""
    if not isinstance(value, dict):
        raise TypeError
    return cast("dict[str, object]", value)


def _as_monkeypatch(value: object) -> MonkeyPatchLike:
    """Narrow pytest monkeypatch fixture to setenv-capable helper."""
    if not isinstance(value, MonkeyPatchLike):
        raise TypeError
    return value


@runtime_checkable
class MonkeyPatchLike(Protocol):
    """Runtime-checkable subset of pytest monkeypatch fixture behavior."""

    def setenv(self, name: str, value: str) -> None:
        """Set environment variable for duration of current test."""
