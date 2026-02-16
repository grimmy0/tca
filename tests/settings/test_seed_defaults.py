"""Tests for startup seeding of dynamic setting defaults (C020)."""

from __future__ import annotations

import json
import sqlite3
from http import HTTPStatus
from typing import TYPE_CHECKING, Protocol, cast, runtime_checkable

from fastapi.testclient import TestClient

from tca.api.app import create_app
from tca.storage import DYNAMIC_SETTINGS_DEFAULTS

if TYPE_CHECKING:
    from pathlib import Path

SETTINGS_SNAPSHOT_COLUMN_COUNT = 3
DEFAULTS_BY_KEY: dict[str, object] = dict(DYNAMIC_SETTINGS_DEFAULTS)


def test_first_boot_inserts_all_design_default_keys(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure first startup inserts all design-defined dynamic setting defaults."""
    db_path = tmp_path / "seed-defaults-first-boot.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (tmp_path / "seed-defaults-bootstrap-token.txt").as_posix(),
    )

    _boot_app_once()

    snapshot = _read_settings_snapshot(db_path)
    if not set(DEFAULTS_BY_KEY).issubset(set(snapshot)):
        raise AssertionError
    for key, expected_value in DEFAULTS_BY_KEY.items():
        value, _ = snapshot[key]
        if value != expected_value:
            raise AssertionError


def test_second_boot_does_not_overwrite_modified_values(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure rerunning startup seed keeps existing user-edited values."""
    db_path = tmp_path / "seed-defaults-second-boot.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (tmp_path / "seed-defaults-bootstrap-token.txt").as_posix(),
    )

    _boot_app_once()

    key = "scheduler.max_pages_per_poll"
    modified_value = 17
    _update_setting_value(db_path, key=key, value=modified_value)
    before_value, before_updated_at = _read_settings_snapshot(db_path)[key]

    _boot_app_once()

    after_value, after_updated_at = _read_settings_snapshot(db_path)[key]
    if after_value != modified_value:
        raise AssertionError
    if after_updated_at != before_updated_at:
        raise AssertionError
    if before_value != modified_value:
        raise AssertionError


def test_missing_single_key_is_backfilled_without_touching_others(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure startup backfills missing key while leaving other rows unchanged."""
    db_path = tmp_path / "seed-defaults-backfill.sqlite3"
    patcher = _as_monkeypatch(monkeypatch)
    patcher.setenv("TCA_DB_PATH", db_path.as_posix())
    patcher.setenv(
        "TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH",
        (tmp_path / "seed-defaults-bootstrap-token.txt").as_posix(),
    )

    _boot_app_once()

    missing_key = "scheduler.max_messages_per_poll"
    modified_key = "backup.retain_count"
    untouched_key = "retention.items_days"

    modified_value = 9
    _update_setting_value(db_path, key=modified_key, value=modified_value)
    before_snapshot = _read_settings_snapshot(db_path)
    _delete_setting(db_path, key=missing_key)

    _boot_app_once()

    after_snapshot = _read_settings_snapshot(db_path)
    backfilled_value, _ = after_snapshot[missing_key]
    if backfilled_value != DEFAULTS_BY_KEY[missing_key]:
        raise AssertionError

    before_modified_value, before_modified_updated_at = before_snapshot[modified_key]
    after_modified_value, after_modified_updated_at = after_snapshot[modified_key]
    if after_modified_value != modified_value:
        raise AssertionError
    if before_modified_value != modified_value:
        raise AssertionError
    if after_modified_updated_at != before_modified_updated_at:
        raise AssertionError

    if after_snapshot[untouched_key] != before_snapshot[untouched_key]:
        raise AssertionError


def _boot_app_once() -> None:
    """Start app once and assert health endpoint succeeds."""
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/health")
        if response.status_code != HTTPStatus.OK:
            raise AssertionError


def _read_settings_snapshot(db_path: Path) -> dict[str, tuple[object, str]]:
    """Read settings rows as decoded JSON values keyed by setting name."""
    with sqlite3.connect(db_path.as_posix()) as connection:
        cursor = connection.execute(
            "SELECT key, value_json, updated_at FROM settings ORDER BY key",
        )
        rows = cast("list[tuple[object, object, object]]", cursor.fetchall())

    snapshot: dict[str, tuple[object, str]] = {}
    for row in rows:
        if len(row) != SETTINGS_SNAPSHOT_COLUMN_COUNT:
            raise AssertionError
        key_obj, value_json_obj, updated_at_obj = row
        if not isinstance(key_obj, str):
            raise TypeError
        if not isinstance(value_json_obj, str):
            raise TypeError
        if not isinstance(updated_at_obj, str):
            raise TypeError
        snapshot[key_obj] = (cast("object", json.loads(value_json_obj)), updated_at_obj)
    return snapshot


def _update_setting_value(db_path: Path, *, key: str, value: object) -> None:
    """Overwrite a single setting with JSON payload for startup idempotency checks."""
    encoded_value = json.dumps(value, separators=(",", ":"), allow_nan=False)
    with sqlite3.connect(db_path.as_posix()) as connection:
        _ = connection.execute(
            """
            UPDATE settings
            SET value_json = :value_json,
                updated_at = CURRENT_TIMESTAMP
            WHERE key = :key
            """,
            {"key": key, "value_json": encoded_value},
        )
        connection.commit()


def _delete_setting(db_path: Path, *, key: str) -> None:
    """Delete one settings row to exercise single-key backfill behavior."""
    with sqlite3.connect(db_path.as_posix()) as connection:
        _ = connection.execute(
            "DELETE FROM settings WHERE key = :key",
            {"key": key},
        )
        connection.commit()


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
