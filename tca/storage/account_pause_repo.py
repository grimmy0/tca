"""Repository helpers for account-level pause state."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, cast

from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Mapping

    from tca.storage.db import SessionFactory


@dataclass(frozen=True, slots=True)
class AccountPauseRecord:
    """Typed pause state payload for a Telegram account."""

    account_id: int
    paused_at: datetime | None
    pause_reason: str | None


class AccountPauseRepositoryError(RuntimeError):
    """Base exception for account pause operations."""


class AccountPauseDecodeError(AccountPauseRepositoryError):
    """Raised when pause state rows cannot be decoded."""

    @classmethod
    def from_details(cls, *, details: str) -> AccountPauseDecodeError:
        """Build deterministic decode error message."""
        return cls(f"Account pause payload invalid: {details}")


class AccountPauseRepository:
    """Repository for updating account pause state flags."""

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

    async def get_pause_state(
        self,
        *,
        account_id: int,
    ) -> AccountPauseRecord | None:
        """Return pause state for an account or None if missing."""
        statement = text(
            """
            SELECT id, paused_at, pause_reason
            FROM telegram_accounts
            WHERE id = :account_id
            """,
        )
        async with self._read_session_factory() as session:
            result = await session.execute(statement, {"account_id": account_id})
            row = result.mappings().one_or_none()
        if row is None:
            return None
        return _decode_pause_row(row)

    async def pause_account(
        self,
        *,
        account_id: int,
        reason: str,
        paused_at: datetime | None = None,
    ) -> AccountPauseRecord | None:
        """Set pause state for an account and return the updated payload."""
        if paused_at is None:
            paused_at = datetime.now(timezone.utc)
        statement = text(
            """
            UPDATE telegram_accounts
            SET paused_at = :paused_at,
                pause_reason = :pause_reason,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :account_id
            RETURNING id, paused_at, pause_reason
            """,
        )
        async with self._write_session_factory() as session:
            result = await session.execute(
                statement,
                {
                    "account_id": account_id,
                    "paused_at": paused_at,
                    "pause_reason": reason,
                },
            )
            row = result.mappings().one_or_none()
            await session.commit()
        if row is None:
            return None
        return _decode_pause_row(row)

    async def resume_account(
        self,
        *,
        account_id: int,
    ) -> AccountPauseRecord | None:
        """Clear pause state for an account and return updated payload."""
        statement = text(
            """
            UPDATE telegram_accounts
            SET paused_at = NULL,
                pause_reason = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :account_id
            RETURNING id, paused_at, pause_reason
            """,
        )
        async with self._write_session_factory() as session:
            result = await session.execute(statement, {"account_id": account_id})
            row = result.mappings().one_or_none()
            await session.commit()
        if row is None:
            return None
        return _decode_pause_row(row)


def _decode_pause_row(row: object) -> AccountPauseRecord:
    row_map = cast("Mapping[str, object]", row)
    account_id = _coerce_int(value=row_map.get("id"))
    paused_at = _coerce_optional_datetime(value=row_map.get("paused_at"))
    pause_reason = _coerce_optional_str(value=row_map.get("pause_reason"))
    return AccountPauseRecord(
        account_id=account_id,
        paused_at=paused_at,
        pause_reason=pause_reason,
    )


def _coerce_int(*, value: object) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise AccountPauseDecodeError.from_details(details="missing integer `id`")


def _coerce_optional_str(*, value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise AccountPauseDecodeError.from_details(details="invalid pause reason")


def _coerce_optional_datetime(*, value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise AccountPauseDecodeError.from_details(
                details="invalid paused_at value",
            ) from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    raise AccountPauseDecodeError.from_details(details="invalid paused_at value")
