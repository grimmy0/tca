"""Tests for settings API write routing through writer queue."""

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
INITIAL_MAX_PAGES = 5
UPDATED_MAX_PAGES = 8
EXPECTED_SUBMIT_CALLS = 2
EXPECTED_CLOSE_CALLS = 1


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
            "/settings/scheduler.max_pages_per_poll",
            json={"value": INITIAL_MAX_PAGES},
        )
        updated = client.put(
            "/settings/scheduler.max_pages_per_poll",
            json={"value": UPDATED_MAX_PAGES},
        )

    if created.status_code != HTTPStatus.OK:
        raise AssertionError
    if updated.status_code != HTTPStatus.OK:
        raise AssertionError

    created_data = cast("dict[str, object]", created.json())
    updated_data = cast("dict[str, object]", updated.json())
    if created_data.get("key") != "scheduler.max_pages_per_poll":
        raise AssertionError
    if created_data.get("value") != INITIAL_MAX_PAGES:
        raise AssertionError
    if updated_data.get("value") != UPDATED_MAX_PAGES:
        raise AssertionError
    if queue.submit_calls != EXPECTED_SUBMIT_CALLS:
        raise AssertionError
    if queue.close_calls != EXPECTED_CLOSE_CALLS:
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
