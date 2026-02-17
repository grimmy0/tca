"""Repository helpers for poll job queue rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from sqlalchemy import text

if TYPE_CHECKING:
    from tca.storage.db import SessionFactory


@dataclass(slots=True, frozen=True)
class PollJobRecord:
    """Typed poll job payload stored for scheduler processing."""

    job_id: int
    channel_id: int
    correlation_id: str


class PollJobsRepositoryError(RuntimeError):
    """Base exception for poll job repository operations."""


class PollJobsRepository:
    """Insert-only helper for poll job queue rows."""

    _read_session_factory: SessionFactory
    _write_session_factory: SessionFactory

    def __init__(
        self,
        *,
        read_session_factory: SessionFactory,
        write_session_factory: SessionFactory,
    ) -> None:
        """Create repository with explicit read/write session dependencies."""
        self._read_session_factory = read_session_factory
        self._write_session_factory = write_session_factory

    async def enqueue_poll_job(
        self,
        *,
        channel_id: int,
        correlation_id: str,
    ) -> PollJobRecord:
        """Insert a new poll job and return the queued payload."""
        statement = text(
            """
            INSERT INTO poll_jobs (channel_id, correlation_id)
            VALUES (:channel_id, :correlation_id)
            RETURNING id, channel_id, correlation_id
            """,
        )
        async with self._write_session_factory() as session:
            result = await session.execute(
                statement,
                {
                    "channel_id": channel_id,
                    "correlation_id": correlation_id,
                },
            )
            row = result.mappings().one()
            await session.commit()
        return _decode_poll_job_row(row)


def _decode_poll_job_row(row: object) -> PollJobRecord:
    row_map = cast("dict[str, object]", row)
    job_id = row_map.get("id")
    channel_id = row_map.get("channel_id")
    correlation_id = row_map.get("correlation_id")
    if not isinstance(job_id, int):
        raise PollJobsRepositoryError("Poll job row missing id.")
    if not isinstance(channel_id, int):
        raise PollJobsRepositoryError("Poll job row missing channel_id.")
    if not isinstance(correlation_id, str):
        raise PollJobsRepositoryError("Poll job row missing correlation_id.")
    return PollJobRecord(
        job_id=job_id,
        channel_id=channel_id,
        correlation_id=correlation_id,
    )
