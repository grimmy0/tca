"""Flood-wait handling helpers for ingest polling."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable

from tca.storage import (
    ChannelStateRecord,
    ChannelStateRepository,
    NotificationsRepository,
    WriterQueueProtocol,
)

TimeProvider = Callable[[], datetime]

SIGNIFICANT_FLOOD_WAIT_SECONDS = 300
FLOOD_WAIT_NOTIFICATION_TYPE = "ingest.flood_wait"
FLOOD_WAIT_NOTIFICATION_SEVERITY = "medium"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _extract_wait_seconds(*, error: BaseException) -> int | None:
    wait_seconds = getattr(error, "seconds", None)
    if isinstance(wait_seconds, int) and wait_seconds > 0:
        return wait_seconds
    return None


async def handle_flood_wait(
    *,
    writer_queue: WriterQueueProtocol,
    state_repository: ChannelStateRepository,
    notifications_repository: NotificationsRepository,
    channel_id: int,
    error: BaseException,
    time_provider: TimeProvider | None = None,
) -> ChannelStateRecord:
    """Pause channel and optionally emit notification for flood wait errors."""
    wait_seconds = _extract_wait_seconds(error=error)
    if wait_seconds is None:
        raise ValueError("Flood wait error missing wait seconds.")
    now = _normalize_datetime(_utc_now() if time_provider is None else time_provider())
    resume_at = now + timedelta(seconds=wait_seconds)
    should_notify = wait_seconds >= SIGNIFICANT_FLOOD_WAIT_SECONDS

    async def _persist() -> ChannelStateRecord:
        record = await state_repository.update_pause(
            channel_id=channel_id,
            paused_until=resume_at,
        )
        if should_notify:
            await notifications_repository.create(
                notification_type=FLOOD_WAIT_NOTIFICATION_TYPE,
                severity=FLOOD_WAIT_NOTIFICATION_SEVERITY,
                message=(
                    f"Flood wait enforced for channel {channel_id}; "
                    f"paused until {resume_at.isoformat()}."
                ),
                payload={
                    "channel_id": channel_id,
                    "wait_seconds": wait_seconds,
                    "resume_at": resume_at.isoformat(),
                },
            )
        return record

    return await writer_queue.submit(_persist)
