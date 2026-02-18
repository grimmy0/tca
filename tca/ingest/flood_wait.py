"""Flood-wait handling helpers for ingest polling."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from tca.ingest.account_risk import record_account_risk_breach

if TYPE_CHECKING:
    from tca.storage import (
        AccountPauseRepository,
        ChannelStateRecord,
        ChannelStateRepository,
        NotificationsRepository,
        SettingsRepository,
        WriterQueueProtocol,
    )

TimeProvider = Callable[[], datetime]

SIGNIFICANT_FLOOD_WAIT_SECONDS = 300
FLOOD_WAIT_NOTIFICATION_TYPE = "ingest.flood_wait"
FLOOD_WAIT_NOTIFICATION_SEVERITY = "medium"

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _extract_wait_seconds(*, error: BaseException) -> int | None:
    wait_seconds = getattr(error, "seconds", None)
    if isinstance(wait_seconds, int) and wait_seconds > 0:
        return wait_seconds
    return None


async def handle_flood_wait(  # noqa: PLR0913
    *,
    writer_queue: WriterQueueProtocol,
    state_repository: ChannelStateRepository,
    notifications_repository: NotificationsRepository,
    channel_id: int,
    error: BaseException,
    account_id: int | None = None,
    settings_repository: SettingsRepository | None = None,
    pause_repository: AccountPauseRepository | None = None,
    time_provider: TimeProvider | None = None,
) -> ChannelStateRecord:
    """Pause channel and optionally emit notification for flood wait errors."""
    wait_seconds = _extract_wait_seconds(error=error)
    if wait_seconds is None:
        msg = "Flood wait error missing wait seconds."
        raise ValueError(msg)
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

    record = await writer_queue.submit(_persist)
    if (
        account_id is not None
        and settings_repository is not None
        and pause_repository is not None
    ):
        try:
            await record_account_risk_breach(
                writer_queue=writer_queue,
                settings_repository=settings_repository,
                pause_repository=pause_repository,
                notifications_repository=notifications_repository,
                account_id=account_id,
                breach_reason="flood-wait",
                time_provider=time_provider,
            )
        except Exception:
            logger.exception(
                "Failed to record account risk breach after flood wait for account %s",
                account_id,
            )
    return record
