"""Helpers for capturing ingest errors and continuing ingest flow."""

from __future__ import annotations

import asyncio
from enum import StrEnum
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from tca.storage import (
        IngestErrorRecord,
        IngestErrorsRepository,
        WriterQueueProtocol,
    )

T = TypeVar("T")


class IngestErrorStage(StrEnum):
    """Allowed ingest error stages persisted for ops visibility."""

    FETCH = "fetch"
    NORMALIZE = "normalize"
    DEDUPE = "dedupe"
    AUTH = "auth"


ALLOWED_INGEST_ERROR_STAGES = tuple(stage.value for stage in IngestErrorStage)


def normalize_ingest_error_stage(stage: str | IngestErrorStage) -> str:
    """Normalize ingest error stage to persisted enum values."""
    if isinstance(stage, IngestErrorStage):
        value = stage.value
    elif isinstance(stage, str):
        value = stage.strip().lower()
    else:
        msg = "Ingest error stage must be a string or IngestErrorStage."
        raise TypeError(msg)
    if value not in ALLOWED_INGEST_ERROR_STAGES:
        msg = f"Invalid ingest error stage: {stage!r}"
        raise ValueError(msg)
    return value


async def capture_ingest_error(  # noqa: PLR0913
    *,
    writer_queue: WriterQueueProtocol,
    errors_repository: IngestErrorsRepository,
    channel_id: int | None,
    stage: str | IngestErrorStage,
    error_code: str,
    error_message: str,
    payload_ref: str | None = None,
) -> IngestErrorRecord:
    """Persist an ingest error through the writer queue."""
    normalized_stage = normalize_ingest_error_stage(stage)

    async def _persist() -> IngestErrorRecord:
        return await errors_repository.create(
            channel_id=channel_id,
            stage=normalized_stage,
            error_code=error_code,
            error_message=error_message,
            payload_ref=payload_ref,
        )

    return await writer_queue.submit(_persist)


async def execute_with_ingest_error_capture[T](  # noqa: PLR0913
    *,
    operation: Callable[[], Awaitable[T]],
    writer_queue: WriterQueueProtocol,
    errors_repository: IngestErrorsRepository,
    channel_id: int | None,
    stage: str | IngestErrorStage,
    error_code: str,
    payload_ref: str | None = None,
    recoverable_errors: tuple[type[BaseException], ...] = (Exception,),
) -> T | None:
    """Run ingest operation and capture recoverable errors without aborting."""
    try:
        return await operation()
    except recoverable_errors as exc:
        if isinstance(exc, asyncio.CancelledError):
            raise
        await capture_ingest_error(
            writer_queue=writer_queue,
            errors_repository=errors_repository,
            channel_id=channel_id,
            stage=stage,
            error_code=error_code,
            error_message=str(exc),
            payload_ref=payload_ref,
        )
        return None
