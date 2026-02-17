"""Scheduler core loop and lifecycle service."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable
from uuid import uuid4

from tca.storage import (
    ChannelStateRepository,
    ChannelsRepository,
    PollJobRecord,
    PollJobsRepository,
    StorageRuntime,
    WriterQueueProtocol,
)

logger = logging.getLogger(__name__)

TimeProvider = Callable[[], datetime]
CorrelationIdFactory = Callable[[], str]
RuntimeProvider = Callable[[], StorageRuntime]
WriterQueueProvider = Callable[[], WriterQueueProtocol]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _default_correlation_id() -> str:
    return str(uuid4())


def _normalize_now(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now


@dataclass(slots=True)
class SchedulerCoreLoop:
    """Select eligible channels and enqueue poll jobs."""

    channels_repository: ChannelsRepository
    state_repository: ChannelStateRepository
    jobs_repository: PollJobsRepository
    writer_queue: WriterQueueProtocol | None = None
    poll_interval_seconds: int = 300
    time_provider: TimeProvider = _utc_now
    correlation_id_factory: CorrelationIdFactory = _default_correlation_id

    async def run_once(self) -> list[PollJobRecord]:
        """Run one scheduler tick and enqueue eligible channel jobs."""
        now = _normalize_now(self.time_provider())
        eligible = await self._select_eligible_channels(now=now)
        jobs: list[PollJobRecord] = []
        for channel_id in eligible:
            jobs.append(await self._enqueue_job(channel_id=channel_id))
        return jobs

    async def _select_eligible_channels(self, *, now: datetime) -> list[int]:
        channels = await self.channels_repository.list_schedulable_channels()
        channel_ids = [channel.id for channel in channels]
        state_map = await self.state_repository.list_states_by_channel_ids(
            channel_ids=channel_ids,
        )
        eligible: list[int] = []
        for channel_id in channel_ids:
            state = state_map.get(channel_id)
            if self._is_due(state_last_success=state.last_success_at if state else None, now=now):
                eligible.append(channel_id)
        return eligible

    def _is_due(
        self,
        *,
        state_last_success: datetime | None,
        now: datetime,
    ) -> bool:
        if state_last_success is None:
            return True
        next_run_at = state_last_success + timedelta(seconds=self.poll_interval_seconds)
        return next_run_at <= now

    async def _enqueue_job(self, *, channel_id: int) -> PollJobRecord:
        correlation_id = self.correlation_id_factory()

        async def _write_job() -> PollJobRecord:
            return await self.jobs_repository.enqueue_poll_job(
                channel_id=channel_id,
                correlation_id=correlation_id,
            )

        if self.writer_queue is None:
            return await _write_job()
        return await self.writer_queue.submit(_write_job)


@dataclass(slots=True)
class SchedulerService:
    """Lifecycle-managed scheduler loop service."""

    runtime_provider: RuntimeProvider
    writer_queue_provider: WriterQueueProvider | None = None
    poll_interval_seconds: int = 300
    tick_interval_seconds: float = 1.0
    time_provider: TimeProvider = _utc_now
    correlation_id_factory: CorrelationIdFactory = _default_correlation_id
    _task: asyncio.Task[None] | None = None
    _stop_event: asyncio.Event | None = None

    async def startup(self) -> None:
        """Start the scheduler background loop."""
        if self.is_running:
            return
        runtime = self.runtime_provider()
        writer_queue = (
            self.writer_queue_provider()
            if self.writer_queue_provider is not None
            else None
        )
        core_loop = SchedulerCoreLoop(
            channels_repository=ChannelsRepository(
                read_session_factory=runtime.read_session_factory,
                write_session_factory=runtime.write_session_factory,
            ),
            state_repository=ChannelStateRepository(
                read_session_factory=runtime.read_session_factory,
                write_session_factory=runtime.write_session_factory,
            ),
            jobs_repository=PollJobsRepository(
                read_session_factory=runtime.read_session_factory,
                write_session_factory=runtime.write_session_factory,
            ),
            writer_queue=writer_queue,
            poll_interval_seconds=self.poll_interval_seconds,
            time_provider=self.time_provider,
            correlation_id_factory=self.correlation_id_factory,
        )
        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop(core_loop))

    async def shutdown(self) -> None:
        """Stop the scheduler background loop."""
        if self._task is None:
            return
        if self._stop_event is not None:
            self._stop_event.set()
        await self._task
        self._task = None
        self._stop_event = None

    @property
    def is_running(self) -> bool:
        """Return True when the scheduler loop task is active."""
        return self._task is not None and not self._task.done()

    async def _run_loop(self, core_loop: SchedulerCoreLoop) -> None:
        stop_event = self._stop_event
        if stop_event is None:
            return
        while not stop_event.is_set():
            try:
                await core_loop.run_once()
            except Exception:
                logger.exception("Scheduler loop tick failed")
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self.tick_interval_seconds,
                )
            except asyncio.TimeoutError:
                continue
