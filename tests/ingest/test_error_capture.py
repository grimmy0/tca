"""Tests for ingest error capture helpers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

import pytest
from sqlalchemy import text

from tca.config.settings import load_settings
from tca.ingest import (
    ALLOWED_INGEST_ERROR_STAGES,
    IngestErrorStage,
    execute_with_ingest_error_capture,
    normalize_ingest_error_stage,
)
from tca.storage import (
    IngestErrorsRepository,
    StorageRuntime,
    create_storage_runtime,
    dispose_storage_runtime,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from pathlib import Path


@dataclass(slots=True)
class RecordingWriterQueue:
    """Writer queue stub that records ingest error submissions."""

    submit_calls: int = 0

    async def submit(self, operation: Callable[[], Awaitable[object]]) -> object:
        """Record queue submissions and execute the operation inline."""
        self.submit_calls += 1
        return await operation()


@pytest.fixture
async def ingest_error_runtime(
    tmp_path: Path,
) -> AsyncIterator[tuple[IngestErrorsRepository, StorageRuntime]]:
    """Create ingest error repository and schema fixture."""
    db_path = tmp_path / "ingest-errors.sqlite3"
    settings = load_settings({"TCA_DB_PATH": db_path.as_posix()})
    runtime = create_storage_runtime(settings)

    async with runtime.write_engine.begin() as connection:
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS telegram_channels (
                id INTEGER PRIMARY KEY
            )
            """,
        )
        _ = await connection.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS ingest_errors (
                id INTEGER PRIMARY KEY,
                channel_id INTEGER NULL,
                stage VARCHAR(32) NOT NULL,
                error_code VARCHAR(128) NOT NULL,
                error_message TEXT NOT NULL,
                payload_ref TEXT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT ck_ingest_errors_stage
                    CHECK (stage IN ('fetch', 'normalize', 'dedupe', 'auth')),
                CONSTRAINT fk_ingest_errors_channel_id
                    FOREIGN KEY (channel_id)
                    REFERENCES telegram_channels(id)
                    ON DELETE SET NULL
            )
            """,
        )

    try:
        yield (
            IngestErrorsRepository(
                read_session_factory=runtime.read_session_factory,
                write_session_factory=runtime.write_session_factory,
            ),
            runtime,
        )
    finally:
        await dispose_storage_runtime(runtime)


def test_ingest_error_stage_mapping_matches_allowed_values() -> None:
    """Ensure stage normalization matches allowed ingest error stages."""
    for stage in IngestErrorStage:
        normalized = normalize_ingest_error_stage(stage)
        if normalized != stage.value:
            raise AssertionError
        if normalized not in ALLOWED_INGEST_ERROR_STAGES:
            raise AssertionError

    for stage in ALLOWED_INGEST_ERROR_STAGES:
        normalized = normalize_ingest_error_stage(stage.upper())
        if normalized != stage:
            raise AssertionError

    with pytest.raises(TypeError):
        _ = normalize_ingest_error_stage(cast("object", 1))


@pytest.mark.asyncio
async def test_ingest_error_rows_include_non_null_timestamp(
    ingest_error_runtime: tuple[IngestErrorsRepository, StorageRuntime],
) -> None:
    """Ensure ingest error inserts return non-null created_at timestamp."""
    repository, _ = ingest_error_runtime
    record = await repository.create(
        channel_id=None,
        stage="fetch",
        error_code="TEST_CODE",
        error_message="test error",
        payload_ref="payload/1",
    )
    if record.created_at is None:
        raise AssertionError


@pytest.mark.asyncio
async def test_ingest_pipeline_continues_after_recoverable_errors(
    ingest_error_runtime: tuple[IngestErrorsRepository, StorageRuntime],
) -> None:
    """Ensure recoverable ingest errors are captured without aborting."""
    repository, runtime = ingest_error_runtime
    queue = RecordingWriterQueue()

    async def _fail() -> str:
        msg = "recoverable-error"
        raise RuntimeError(msg)

    result = await execute_with_ingest_error_capture(
        operation=_fail,
        writer_queue=queue,
        errors_repository=repository,
        channel_id=None,
        stage=IngestErrorStage.FETCH,
        error_code="FETCH_FAIL",
        payload_ref="payload/2",
        recoverable_errors=(RuntimeError,),
    )
    if result is not None:
        raise AssertionError

    async def _success() -> str:
        return "ok"

    result = await execute_with_ingest_error_capture(
        operation=_success,
        writer_queue=queue,
        errors_repository=repository,
        channel_id=None,
        stage=IngestErrorStage.FETCH,
        error_code="FETCH_OK",
        payload_ref=None,
        recoverable_errors=(RuntimeError,),
    )
    if result != "ok":
        raise AssertionError
    if queue.submit_calls != 1:
        raise AssertionError

    async with runtime.read_session_factory() as session:
        result = await session.execute(
            text("SELECT COUNT(*) AS count FROM ingest_errors"),
        )
        count = result.mappings().one()["count"]
    if count != 1:
        raise AssertionError


@pytest.mark.asyncio
async def test_ingest_pipeline_does_not_swallow_cancellation(
    ingest_error_runtime: tuple[IngestErrorsRepository, StorageRuntime],
) -> None:
    """Ensure cancellations propagate instead of being captured as errors."""
    repository, runtime = ingest_error_runtime
    queue = RecordingWriterQueue()

    async def _cancel() -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await execute_with_ingest_error_capture(
            operation=_cancel,
            writer_queue=queue,
            errors_repository=repository,
            channel_id=None,
            stage=IngestErrorStage.FETCH,
            error_code="FETCH_CANCEL",
            payload_ref=None,
            recoverable_errors=(Exception,),
        )

    if queue.submit_calls != 0:
        raise AssertionError

    async with runtime.read_session_factory() as session:
        result = await session.execute(
            text("SELECT COUNT(*) AS count FROM ingest_errors"),
        )
        count = result.mappings().one()["count"]
    if count != 0:
        raise AssertionError
