"""Tests for settings API allowlist, persistence, and writer queue routing."""

from __future__ import annotations

from dataclasses import dataclass
from http import HTTPStatus
from typing import TYPE_CHECKING, Protocol, TypeVar, cast, runtime_checkable

from fastapi.testclient import TestClient

from tca.api.app import create_app

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

T = TypeVar("T")
ALLOWED_SETTINGS_KEY = "scheduler.max_pages_per_poll"
UNKNOWN_SETTINGS_KEY = "scheduler.not_a_real_key"
INITIAL_MAX_PAGES = 5
UPDATED_MAX_PAGES = 8
RESTART_UPDATED_MAX_PAGES = 12
HORIZON_SETTINGS_KEY = "dedupe.default_horizon_minutes"
UPDATED_HORIZON_MINUTES = 360
EXPECTED_SUBMIT_CALLS = 2
EXPECTED_CLOSE_CALLS = 1
EXPECTED_BAD_REQUEST_STATUS = HTTPStatus.BAD_REQUEST
EXPECTED_SUCCESS_STATUS = HTTPStatus.OK


@dataclass(slots=True)
class RecordingWriterQueue:
    """Writer queue stub that records submit and close lifecycle activity."""

    submit_calls: int = 0
    close_calls: int = 0

    async def submit(self, operation: Callable[[], Awaitable[T]]) -> T:
        """Record queue usage and execute provided write operation."""
        self.submit_calls += 1
        return await operation()

    async def close(self) -> None:
        """Record queue close calls from app lifespan shutdown."""
        self.close_calls += 1


def test_put_settings_writes_execute_through_app_writer_queue(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure settings mutating API path runs through configured writer queue."""
    db_path = tmp_path / "settings-api.sqlite3"
    _as_monkeypatch(monkeypatch).setenv("TCA_DB_PATH", db_path.as_posix())

    app = create_app()
    queue = RecordingWriterQueue()
    app.state.writer_queue_factory = lambda: queue

    with TestClient(app) as client:
        created = client.put(
            f"/settings/{ALLOWED_SETTINGS_KEY}",
            json={"value": INITIAL_MAX_PAGES},
        )
        updated = client.put(
            f"/settings/{ALLOWED_SETTINGS_KEY}",
            json={"value": UPDATED_MAX_PAGES},
        )

    if created.status_code != EXPECTED_SUCCESS_STATUS:
        raise AssertionError
    if updated.status_code != EXPECTED_SUCCESS_STATUS:
        raise AssertionError

    created_data = cast("dict[str, object]", created.json())
    updated_data = cast("dict[str, object]", updated.json())
    if created_data.get("key") != ALLOWED_SETTINGS_KEY:
        raise AssertionError
    if created_data.get("value") != INITIAL_MAX_PAGES:
        raise AssertionError
    if updated_data.get("value") != UPDATED_MAX_PAGES:
        raise AssertionError
    if queue.submit_calls != EXPECTED_SUBMIT_CALLS:
        raise AssertionError
    if queue.close_calls != EXPECTED_CLOSE_CALLS:
        raise AssertionError


def test_unknown_setting_keys_are_rejected_with_bad_request(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure settings API rejects unknown keys on read and write with 400."""
    db_path = tmp_path / "settings-api-unknown-key.sqlite3"
    _as_monkeypatch(monkeypatch).setenv("TCA_DB_PATH", db_path.as_posix())

    app = create_app()
    with TestClient(app) as client:
        write_response = client.put(
            f"/settings/{UNKNOWN_SETTINGS_KEY}",
            json={"value": 123},
        )
        read_response = client.get(f"/settings/{UNKNOWN_SETTINGS_KEY}")

    if write_response.status_code != EXPECTED_BAD_REQUEST_STATUS:
        raise AssertionError
    if read_response.status_code != EXPECTED_BAD_REQUEST_STATUS:
        raise AssertionError

    expected_detail = f"Unknown setting key '{UNKNOWN_SETTINGS_KEY}'."
    write_data = cast("dict[str, object]", write_response.json())
    read_data = cast("dict[str, object]", read_response.json())
    if write_data.get("detail") != expected_detail:
        raise AssertionError
    if read_data.get("detail") != expected_detail:
        raise AssertionError


def test_allowed_setting_key_updates_immediately_and_persists_across_restart(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure allowlisted key writes are immediately readable and persisted."""
    db_path = tmp_path / "settings-api-persist.sqlite3"
    _as_monkeypatch(monkeypatch).setenv("TCA_DB_PATH", db_path.as_posix())

    app = create_app()
    with TestClient(app) as client:
        updated = client.put(
            f"/settings/{ALLOWED_SETTINGS_KEY}",
            json={"value": RESTART_UPDATED_MAX_PAGES},
        )
        immediate_read = client.get(f"/settings/{ALLOWED_SETTINGS_KEY}")

    if updated.status_code != EXPECTED_SUCCESS_STATUS:
        raise AssertionError
    if immediate_read.status_code != EXPECTED_SUCCESS_STATUS:
        raise AssertionError

    updated_data = cast("dict[str, object]", updated.json())
    immediate_data = cast("dict[str, object]", immediate_read.json())
    if updated_data.get("value") != RESTART_UPDATED_MAX_PAGES:
        raise AssertionError
    if immediate_data.get("value") != RESTART_UPDATED_MAX_PAGES:
        raise AssertionError

    restarted_app = create_app()
    with TestClient(restarted_app) as restarted_client:
        persisted_read = restarted_client.get(f"/settings/{ALLOWED_SETTINGS_KEY}")

    if persisted_read.status_code != EXPECTED_SUCCESS_STATUS:
        raise AssertionError
    persisted_data = cast("dict[str, object]", persisted_read.json())
    if persisted_data.get("value") != RESTART_UPDATED_MAX_PAGES:
        raise AssertionError


def test_put_setting_returns_effective_value_after_write(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Ensure PUT response returns the same effective value as subsequent GET."""
    db_path = tmp_path / "settings-api-effective-value.sqlite3"
    _as_monkeypatch(monkeypatch).setenv("TCA_DB_PATH", db_path.as_posix())

    app = create_app()
    with TestClient(app) as client:
        write_response = client.put(
            f"/settings/{HORIZON_SETTINGS_KEY}",
            json={"value": UPDATED_HORIZON_MINUTES},
        )
        read_response = client.get(f"/settings/{HORIZON_SETTINGS_KEY}")

    if write_response.status_code != EXPECTED_SUCCESS_STATUS:
        raise AssertionError
    if read_response.status_code != EXPECTED_SUCCESS_STATUS:
        raise AssertionError

    write_data = cast("dict[str, object]", write_response.json())
    read_data = cast("dict[str, object]", read_response.json())
    if write_data.get("key") != HORIZON_SETTINGS_KEY:
        raise AssertionError
    if write_data.get("value") != UPDATED_HORIZON_MINUTES:
        raise AssertionError
    if read_data.get("value") != write_data.get("value"):
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
