"""Single-writer queue service for serializing async write operations."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, TypeVar, cast

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

T = TypeVar("T")


class WriterQueueProtocol(Protocol):
    """Protocol for async write queue submit behavior."""

    async def submit(self, operation: Callable[[], Awaitable[T]]) -> T:
        """Enqueue one async operation and await its completion."""
        ...


@dataclass(slots=True)
class _QueuedWriteJob:
    """One queued write job with completion future."""

    operation: Callable[[], Awaitable[object]]
    completion: asyncio.Future[object]


class WriterQueueClosedError(RuntimeError):
    """Raised when callers submit work after queue shutdown."""

    @classmethod
    def default_message(cls) -> WriterQueueClosedError:
        """Build deterministic error text for closed queue submissions."""
        return cls("Writer queue is closed and cannot accept new jobs.")


class WriterQueue:
    """Serialize write jobs through one in-process worker task."""

    _queue: asyncio.Queue[_QueuedWriteJob | None]
    _worker_task: asyncio.Task[None] | None
    _lifecycle_lock: asyncio.Lock
    _closed: bool

    def __init__(self) -> None:
        """Initialize queue state with lazy worker startup."""
        self._queue = asyncio.Queue()
        self._worker_task = None
        self._lifecycle_lock = asyncio.Lock()
        self._closed = False

    async def submit(self, operation: Callable[[], Awaitable[T]]) -> T:
        """Submit a write operation and await deterministic result/exception."""
        completion: asyncio.Future[object] = asyncio.get_running_loop().create_future()
        queued_job = _QueuedWriteJob(
            operation=cast("Callable[[], Awaitable[object]]", operation),
            completion=completion,
        )
        async with self._lifecycle_lock:
            if self._closed:
                raise WriterQueueClosedError.default_message()
            self._ensure_worker_locked()
            await self._queue.put(queued_job)

        result = await completion
        return cast("T", result)

    async def close(self) -> None:
        """Stop accepting new jobs and drain queued writes before returning."""
        worker: asyncio.Task[None] | None
        async with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            worker = self._worker_task
            if worker is None:
                return
            await self._queue.put(None)

        await worker
        self._worker_task = None

    def _ensure_worker_locked(self) -> None:
        """Start a worker task when one is not already running."""
        current_worker = self._worker_task
        if current_worker is not None and not current_worker.done():
            return
        self._worker_task = asyncio.create_task(self._run_worker())

    async def _run_worker(self) -> None:
        """Execute queued write operations one at a time in FIFO order."""
        while True:
            queued_job = await self._queue.get()
            try:
                if queued_job is None:
                    return
                await self._execute_queued_job(queued_job)
            finally:
                self._queue.task_done()

    async def _execute_queued_job(self, queued_job: _QueuedWriteJob) -> None:
        """Resolve queued completion future with operation result or error."""
        try:
            result = await queued_job.operation()
        except Exception as exc:  # noqa: BLE001
            if queued_job.completion.cancelled():
                return
            queued_job.completion.set_exception(exc)
            return

        if queued_job.completion.cancelled():
            return
        queued_job.completion.set_result(result)
