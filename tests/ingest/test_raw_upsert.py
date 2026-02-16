"""Tests for ingest raw upsert queue routing behavior."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, TypeVar

import pytest

from tca.ingest import upsert_raw_message

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

T = TypeVar("T")


def _empty_calls() -> list[tuple[int, int, object]]:
    """Build typed empty call-list for dataclass default factory."""
    return []


@dataclass(slots=True)
class RecordingWriterQueue:
    """Writer queue stub that records submit usage for ingest writes."""

    submit_calls: int = 0

    async def submit(self, operation: Callable[[], Awaitable[T]]) -> T:
        """Record queue submit and execute operation."""
        self.submit_calls += 1
        return await operation()


@dataclass(slots=True)
class RecordingRawMessageRepository:
    """Ingest raw-message write stub for deterministic upsert assertions."""

    calls: list[tuple[int, int, object]] = field(default_factory=_empty_calls)
    return_value: object = "upserted"
    error: Exception | None = None

    async def upsert_raw_message(
        self,
        *,
        channel_id: int,
        message_id: int,
        payload: object,
    ) -> object:
        """Record upsert call and optionally raise configured deterministic error."""
        self.calls.append((channel_id, message_id, payload))
        if self.error is not None:
            raise self.error
        return self.return_value


@pytest.mark.asyncio
async def test_raw_upsert_uses_writer_queue_for_ingest_write_serialization() -> None:
    """Ensure ingest write path calls writer queue instead of direct repository call."""
    queue = RecordingWriterQueue()
    repository = RecordingRawMessageRepository(return_value={"id": 11})

    result = await upsert_raw_message(
        queue,
        repository,
        channel_id=77,
        message_id=9001,
        payload={"text": "hello"},
    )

    if result != {"id": 11}:
        raise AssertionError
    if queue.submit_calls != 1:
        raise AssertionError
    if repository.calls != [(77, 9001, {"text": "hello"})]:
        raise AssertionError


@pytest.mark.asyncio
async def test_raw_upsert_propagates_repository_error_deterministically() -> None:
    """Ensure ingest write failures surface through queue submit completion path."""
    queue = RecordingWriterQueue()
    repository = RecordingRawMessageRepository(
        error=RuntimeError("forced-upsert-error"),
    )

    with pytest.raises(RuntimeError, match="forced-upsert-error"):
        _ = await upsert_raw_message(
            queue,
            repository,
            channel_id=5,
            message_id=6,
            payload={"text": "fail"},
        )

    if queue.submit_calls != 1:
        raise AssertionError
    if repository.calls != [(5, 6, {"text": "fail"})]:
        raise AssertionError
