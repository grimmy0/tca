"""Temporary auth session state storage for Telegram login wizard."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Mapping

    from tca.storage.db import SessionFactory

AUTH_SESSION_TTL_SECONDS = 900


class AuthSessionStateError(RuntimeError):
    """Base error for auth session state storage operations."""


class AuthSessionStateNotFoundError(AuthSessionStateError):
    """Raised when auth session state is missing."""

    @classmethod
    def for_session_id(cls, session_id: str) -> AuthSessionStateNotFoundError:
        """Build deterministic missing-session error."""
        return cls(f"Auth session state not found for session_id='{session_id}'.")


class AuthSessionExpiredError(AuthSessionStateError):
    """Raised when auth session state has expired."""

    @classmethod
    def for_session_id(cls, session_id: str) -> AuthSessionExpiredError:
        """Build deterministic expired-session error."""
        return cls(f"Auth session state expired for session_id='{session_id}'.")


class AuthSessionStateConfigError(AuthSessionStateError):
    """Raised when auth session state inputs are invalid."""

    @classmethod
    def invalid_expiry(cls) -> AuthSessionStateConfigError:
        """Build deterministic error for conflicting expiry inputs."""
        return cls("Auth session expiry must use either expires_at or ttl_seconds.")


@dataclass(frozen=True, slots=True)
class AuthSessionState:
    """Persisted auth session state for multi-step login."""

    session_id: str
    phone_number: str
    status: str
    expires_at: int
    telegram_session: str | None


class AuthSessionStateRepository:
    """Repository for managing temporary auth session state."""

    _read_session_factory: SessionFactory
    _write_session_factory: SessionFactory

    def __init__(
        self,
        *,
        read_session_factory: SessionFactory,
        write_session_factory: SessionFactory,
    ) -> None:
        """Initialize repository with explicit session dependencies."""
        self._read_session_factory = read_session_factory
        self._write_session_factory = write_session_factory

    async def create_session(
        self,
        *,
        session_id: str,
        phone_number: str,
        status: str,
        telegram_session: str | None = None,
        expires_at: int | None = None,
        ttl_seconds: int | None = None,
    ) -> AuthSessionState:
        """Persist a new auth session state row."""
        if expires_at is None:
            ttl = AUTH_SESSION_TTL_SECONDS if ttl_seconds is None else ttl_seconds
            expires_at = _now_epoch() + ttl
        elif ttl_seconds is not None:
            raise AuthSessionStateConfigError.invalid_expiry()

        statement = text(
            """
            INSERT INTO auth_session_state (
                session_id,
                phone_number,
                status,
                expires_at,
                telegram_session
            )
            VALUES (:session_id, :phone_number, :status, :expires_at, :telegram_session)
            RETURNING session_id, phone_number, status, expires_at, telegram_session
            """,
        )
        async with self._write_session_factory() as session:
            result = await session.execute(
                statement,
                {
                    "session_id": session_id,
                    "phone_number": phone_number,
                    "status": status,
                    "expires_at": expires_at,
                    "telegram_session": telegram_session,
                },
            )
            row = result.mappings().one()
            await session.commit()
        return _decode_row(row)

    async def get_session(self, *, session_id: str) -> AuthSessionState:
        """Fetch auth session state by session id, enforcing expiry."""
        statement = text(
            """
            SELECT session_id, phone_number, status, expires_at, telegram_session
            FROM auth_session_state
            WHERE session_id = :session_id
            """,
        )
        async with self._read_session_factory() as session:
            result = await session.execute(statement, {"session_id": session_id})
            row = result.mappings().one_or_none()
        if row is None:
            raise AuthSessionStateNotFoundError.for_session_id(session_id)

        state = _decode_row(row)
        if _is_expired(state.expires_at):
            _ = await self.delete_session(session_id=session_id)
            raise AuthSessionExpiredError.for_session_id(session_id)
        return state

    async def update_status(
        self,
        *,
        session_id: str,
        status: str,
        telegram_session: str | None = None,
        update_session: bool = False,
    ) -> AuthSessionState:
        """Update the status for an auth session state row."""
        values: dict[str, object] = {"session_id": session_id, "status": status}
        if not update_session:
            statement = text(
                """
                UPDATE auth_session_state
                SET status = :status,
                    updated_at = CURRENT_TIMESTAMP
                WHERE session_id = :session_id
                RETURNING session_id, phone_number, status, expires_at, telegram_session
                """,
            )
        else:
            statement = text(
                """
                UPDATE auth_session_state
                SET status = :status,
                    telegram_session = :telegram_session,
                    updated_at = CURRENT_TIMESTAMP
                WHERE session_id = :session_id
                RETURNING session_id, phone_number, status, expires_at, telegram_session
                """,
            )
            values["telegram_session"] = telegram_session
        async with self._write_session_factory() as session:
            result = await session.execute(statement, values)
            row = result.mappings().one_or_none()
            await session.commit()
        if row is None:
            raise AuthSessionStateNotFoundError.for_session_id(session_id)
        state = _decode_row(row)
        if _is_expired(state.expires_at):
            _ = await self.delete_session(session_id=session_id)
            raise AuthSessionExpiredError.for_session_id(session_id)
        return state

    async def delete_session(self, *, session_id: str) -> bool:
        """Delete a session state row and return True if removed."""
        statement = text(
            """
            DELETE FROM auth_session_state
            WHERE session_id = :session_id
            RETURNING session_id
            """,
        )
        async with self._write_session_factory() as session:
            result = await session.execute(statement, {"session_id": session_id})
            row = result.mappings().one_or_none()
            await session.commit()
        return row is not None


def _decode_row(row: object) -> AuthSessionState:
    """Decode a row mapping into an AuthSessionState."""
    row_map = cast("Mapping[str, object]", cast("object", row))
    return AuthSessionState(
        session_id=_coerce_str(value=row_map.get("session_id"), field_name="session_id"),
        phone_number=_coerce_str(
            value=row_map.get("phone_number"),
            field_name="phone_number",
        ),
        status=_coerce_str(value=row_map.get("status"), field_name="status"),
        expires_at=_coerce_int(value=row_map.get("expires_at"), field_name="expires_at"),
        telegram_session=_coerce_optional_str(
            value=row_map.get("telegram_session"),
            field_name="telegram_session",
        ),
    )


def _coerce_str(*, value: object, field_name: str) -> str:
    """Normalize and validate a string column value."""
    if isinstance(value, str):
        return value
    raise AuthSessionStateError(f"Auth session state missing `{field_name}` value.")


def _coerce_int(*, value: object, field_name: str) -> int:
    """Normalize and validate an integer column value."""
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    raise AuthSessionStateError(f"Auth session state missing `{field_name}` value.")


def _coerce_optional_str(*, value: object, field_name: str) -> str | None:
    """Normalize and validate optional string column values."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise AuthSessionStateError(f"Auth session state invalid `{field_name}` value.")


def _now_epoch() -> int:
    """Return the current UTC timestamp as integer seconds."""
    return int(datetime.now(tz=UTC).timestamp())


def _is_expired(expires_at: int) -> bool:
    """Return True when the expiry timestamp has elapsed."""
    return expires_at <= _now_epoch()
