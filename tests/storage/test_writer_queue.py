"""Tests for single-writer queue serialization semantics."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from tca.storage import WriterQueue

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

TOTAL_JOBS = 16
FIFO_JOB_COUNT = 5
EXPECTED_MAX_ACTIVE_JOBS = 1
EXPECTED_FIFO_ORDER = [0, 1, 2, 3, 4]
FIRST_FAILURE_INDEX = 1
SECOND_FAILURE_INDEX = 3
FIRST_FAILURE_MESSAGE = "boom-1"
SECOND_FAILURE_MESSAGE = "boom-3"


@pytest.mark.asyncio
async def test_writer_queue_executes_only_one_job_at_a_time() -> None:
    """Ensure concurrent queue submissions never execute write jobs in parallel."""
    queue = WriterQueue()
    active_jobs: int = 0
    max_active_jobs: int = 0

    def _build_job(index: int) -> Callable[[], Awaitable[int]]:
        async def _job() -> int:
            nonlocal active_jobs
            nonlocal max_active_jobs
            _ = index
            active_jobs += 1
            max_active_jobs = max(max_active_jobs, active_jobs)
            await asyncio.sleep(0.01)
            active_jobs -= 1
            return index

        return _job

    tasks = [
        asyncio.create_task(queue.submit(_build_job(index)))
        for index in range(TOTAL_JOBS)
    ]
    try:
        _ = await asyncio.gather(*tasks)
    finally:
        await queue.close()

    if max_active_jobs != EXPECTED_MAX_ACTIVE_JOBS:  # pyright: ignore[reportUnnecessaryComparison]
        raise AssertionError


@pytest.mark.asyncio
async def test_writer_queue_processes_fifo_and_preserves_result_error_outcomes() -> (
    None
):
    """Ensure FIFO execution yields deterministic completion and error propagation."""
    queue = WriterQueue()
    execution_order: list[int] = []
    completion_order: list[int] = []

    def _build_job(index: int) -> Callable[[], Awaitable[str]]:
        async def _job() -> str:
            execution_order.append(index)
            await asyncio.sleep(0.005)
            if index == FIRST_FAILURE_INDEX:
                message = FIRST_FAILURE_MESSAGE
                raise RuntimeError(message)
            if index == SECOND_FAILURE_INDEX:
                message = SECOND_FAILURE_MESSAGE
                raise RuntimeError(message)
            return f"ok-{index}"

        return _job

    async def _submit(index: int) -> str:
        await asyncio.sleep(index * 0.001)
        try:
            result = await queue.submit(_build_job(index))
        finally:
            completion_order.append(index)
        return result

    tasks = [asyncio.create_task(_submit(index)) for index in range(FIFO_JOB_COUNT)]
    try:
        results = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await queue.close()

    if execution_order != EXPECTED_FIFO_ORDER:
        raise AssertionError
    if completion_order != EXPECTED_FIFO_ORDER:
        raise AssertionError
    if results[0] != "ok-0" or results[2] != "ok-2" or results[4] != "ok-4":
        raise AssertionError
    _assert_runtime_error(results[1], expected_message=FIRST_FAILURE_MESSAGE)
    _assert_runtime_error(results[3], expected_message=SECOND_FAILURE_MESSAGE)


def _assert_runtime_error(result: object, *, expected_message: str) -> None:
    """Verify deterministic runtime-error result payloads."""
    if not isinstance(result, RuntimeError):
        details = f"Expected RuntimeError, got {type(result).__name__}."
        raise TypeError(details)
    if str(result) != expected_message:
        raise AssertionError
