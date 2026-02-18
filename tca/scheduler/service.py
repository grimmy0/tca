"""Scheduler core loop and lifecycle service."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from tca.storage import (
    ChannelsRepository,
    ChannelStateRepository,
    PollJobRecord,
    PollJobsRepository,
    SettingsRepository,
    StorageRuntime,
    WriterQueueProtocol,
)

logger = logging.getLogger(__name__)

TimeProvider = Callable[[], datetime]
CorrelationIdFactory = Callable[[], str]
RuntimeProvider = Callable[[], StorageRuntime]
WriterQueueProvider = Callable[[], WriterQueueProtocol]

DEFAULT_POLL_INTERVAL_SECONDS = 300
DEFAULT_JITTER_RATIO = 0.2
POLL_INTERVAL_SETTING_KEY = "scheduler.default_poll_interval_seconds"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _default_correlation_id() -> str:
    return str(uuid4())


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


@dataclass(slots=True)
class SchedulerCoreLoop:
    """Select eligible channels and enqueue poll jobs."""

    channels_repository: ChannelsRepository
    state_repository: ChannelStateRepository
    jobs_repository: PollJobsRepository
    writer_queue: WriterQueueProtocol | None = None
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS
    jitter_ratio: float = DEFAULT_JITTER_RATIO
    jitter_rng: random.Random = field(default_factory=random.Random)
    time_provider: TimeProvider = _utc_now
    correlation_id_factory: CorrelationIdFactory = _default_correlation_id

    async def run_once(self) -> list[PollJobRecord]:
        """Run one scheduler tick and enqueue eligible channel jobs."""
        now = _normalize_datetime(self.time_provider())
        eligible = await self._select_eligible_channels(now=now)
        return [
            await self._enqueue_job(channel_id=channel_id) for channel_id in eligible
        ]

    async def _select_eligible_channels(self, *, now: datetime) -> list[int]:
        channels = await self.channels_repository.list_schedulable_channels()
        channel_ids = [channel.id for channel in channels]
        state_map = await self.state_repository.list_states_by_channel_ids(
            channel_ids=channel_ids,
        )
        eligible: list[int] = []
        for channel_id in channel_ids:
            state = state_map.get(channel_id)
            if self._is_paused(
                paused_until=state.paused_until if state else None,
                now=now,
            ):
                continue
            if self._is_due(
                state_last_success=state.last_success_at if state else None,
                now=now,
            ):
                eligible.append(channel_id)
        return eligible

    def _is_paused(
        self,
        *,
        paused_until: datetime | None,
        now: datetime,
    ) -> bool:
        if paused_until is None:
            return False
        return _normalize_datetime(paused_until) > now

    def _is_due(
        self,
        *,
        state_last_success: datetime | None,
        now: datetime,
    ) -> bool:
        if state_last_success is None:
            return True
        next_run_at = self._compute_next_run_at(
            state_last_success=state_last_success,
        )
        return next_run_at <= now

    def _compute_next_run_at(self, *, state_last_success: datetime) -> datetime:
        state_last_success = _normalize_datetime(state_last_success)
        jitter_seconds = self._compute_jitter_seconds()
        return state_last_success + timedelta(
            seconds=self.poll_interval_seconds + jitter_seconds,
        )

    def _compute_jitter_seconds(self) -> float:
        jitter_range = self.poll_interval_seconds * self.jitter_ratio
        return self.jitter_rng.uniform(-jitter_range, jitter_range)

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
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS
    jitter_ratio: float = DEFAULT_JITTER_RATIO
    jitter_rng: random.Random = field(default_factory=random.Random)
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
        settings_repository = SettingsRepository(
            read_session_factory=runtime.read_session_factory,
            write_session_factory=runtime.write_session_factory,
        )
        poll_interval_seconds = await _resolve_poll_interval_seconds(
            repository=settings_repository,
            default_value=self.poll_interval_seconds,
        )
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
            poll_interval_seconds=poll_interval_seconds,
            jitter_ratio=self.jitter_ratio,
            jitter_rng=self.jitter_rng,
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
                _ = await core_loop.run_once()
            except Exception as exc:
                if isinstance(exc, asyncio.CancelledError):
                    raise
                logger.exception("Scheduler loop tick failed")
            try:
                _ = await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self.tick_interval_seconds,
                )
            except TimeoutError:
                continue


async def _resolve_poll_interval_seconds(
    *,
    repository: SettingsRepository,
    default_value: int,
) -> int:
    record = await repository.get_by_key(key=POLL_INTERVAL_SETTING_KEY)
    if record is None:
        return default_value
    value = record.value
    if isinstance(value, bool):
        return default_value
    if isinstance(value, int):
        return value if value > 0 else default_value
    if isinstance(value, float):
        if value <= 0:
            return default_value
        resolved = int(value)
        return resolved if resolved > 0 else default_value
    return default_value
