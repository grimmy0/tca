"""Crash-safe key rotation metadata tracking."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from tca.storage.db import SessionFactory


class KeyRotationError(RuntimeError):
    """Base error for key rotation metadata operations."""


class KeyRotationStateMissingError(KeyRotationError):
    """Raised when rotation metadata is missing for an in-progress action."""

    @classmethod
    def default(cls) -> KeyRotationStateMissingError:
        """Build deterministic error when no rotation metadata row exists."""
        return cls("Key rotation metadata is missing.")


class KeyRotationAccountNotFoundError(KeyRotationError):
    """Raised when a requested account row is missing during rotation."""

    @classmethod
    def for_account_id(cls, account_id: int) -> KeyRotationAccountNotFoundError:
        """Build deterministic missing-account error for rotation paths."""
        return cls(
            f"Unable to rotate keys: no account row exists for id={account_id}.",
        )


@dataclass(frozen=True, slots=True)
class KeyRotationState:
    """Persisted rotation metadata for crash-safe resume."""

    target_key_version: int
    last_rotated_account_id: int
    completed_at: object | None


class KeyRotationRepository:
    """Repository for crash-safe key rotation metadata and progress."""

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

    async def begin_rotation(self, *, target_key_version: int) -> KeyRotationState:
        """Create rotation metadata row if missing and return its state."""
        _validate_target_version(target_key_version=target_key_version)
        existing = await self.get_state()
        if existing is not None:
            if existing.target_key_version == target_key_version:
                return existing
            if existing.completed_at is None:
                msg = (
                    "Key rotation already in progress for target version "
                    f"{existing.target_key_version}."
                )
                raise KeyRotationError(
                    msg,
                )
            if target_key_version <= existing.target_key_version:
                msg = (
                    "Key rotation target version must be greater than the "
                    "completed target version."
                )
                raise KeyRotationError(
                    msg,
                )
            reset_statement = text(
                """
                UPDATE auth_key_rotation
                SET target_key_version = :target_key_version,
                    last_rotated_account_id = 0,
                    started_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP,
                    completed_at = NULL
                WHERE id = 1
                """,
            )
            async with self._write_session_factory() as session:
                _ = await session.execute(
                    reset_statement,
                    {"target_key_version": target_key_version},
                )
                await session.commit()
            state = await self.get_state()
            if state is None:
                raise KeyRotationStateMissingError.default()
            return state

        statement = text(
            """
            INSERT INTO auth_key_rotation (
                id,
                target_key_version,
                last_rotated_account_id,
                started_at,
                updated_at
            )
            VALUES (1, :target_key_version, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
        )
        async with self._write_session_factory() as session:
            try:
                _ = await session.execute(
                    statement,
                    {"target_key_version": target_key_version},
                )
                await session.commit()
            except IntegrityError:
                await session.rollback()
                state = await self.get_state()
                if state is not None:
                    return state
                raise
        state = await self.get_state()
        if state is None:
            raise KeyRotationStateMissingError.default()
        return state

    async def get_state(self) -> KeyRotationState | None:
        """Return the persisted rotation metadata row, if present."""
        statement = text(
            """
            SELECT target_key_version,
                   last_rotated_account_id,
                   completed_at
            FROM auth_key_rotation
            WHERE id = 1
            """,
        )
        async with self._read_session_factory() as session:
            result = await session.execute(statement)
            row = result.mappings().one_or_none()
        if row is None:
            return None
        row_map = cast("Mapping[str, object]", cast("object", row))
        return KeyRotationState(
            target_key_version=_coerce_int(
                value=row_map.get("target_key_version"),
                field_name="target_key_version",
            ),
            last_rotated_account_id=_coerce_int(
                value=row_map.get("last_rotated_account_id"),
                field_name="last_rotated_account_id",
            ),
            completed_at=row_map.get("completed_at"),
        )

    async def next_pending_account_id(self) -> int | None:
        """Return the next pending account id for rotation, if any."""
        state = await self.get_state()
        if state is None:
            return None
        statement = text(
            """
            SELECT id
            FROM telegram_accounts
            WHERE key_version < :target_key_version
              AND id > :last_rotated_account_id
            ORDER BY id ASC
            LIMIT 1
            """,
        )
        async with self._read_session_factory() as session:
            result = await session.execute(
                statement,
                {
                    "target_key_version": state.target_key_version,
                    "last_rotated_account_id": state.last_rotated_account_id,
                },
            )
            row = result.mappings().one_or_none()
        if row is None:
            return None
        row_map = cast("Mapping[str, object]", cast("object", row))
        return _coerce_int(value=row_map.get("id"), field_name="id")

    async def mark_account_rotated(self, *, account_id: int) -> None:
        """Mark an account row as rotated and persist progress metadata."""
        state = await self.get_state()
        if state is None:
            raise KeyRotationStateMissingError.default()
        lookup_statement = text(
            """
            SELECT key_version
            FROM telegram_accounts
            WHERE id = :account_id
            """,
        )
        update_statement = text(
            """
            UPDATE telegram_accounts
            SET key_version = :target_key_version,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :account_id
              AND key_version < :target_key_version
            """,
        )
        progress_statement = text(
            """
            UPDATE auth_key_rotation
            SET last_rotated_account_id = :account_id,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
            """,
        )
        async with self._write_session_factory() as session:
            result = await session.execute(
                lookup_statement,
                {"account_id": account_id},
            )
            row = result.mappings().one_or_none()
            if row is None:
                await session.rollback()
                raise KeyRotationAccountNotFoundError.for_account_id(account_id)
            row_map = cast("Mapping[str, object]", cast("object", row))
            key_version = _coerce_int(
                value=row_map.get("key_version"),
                field_name="key_version",
            )
            if key_version < state.target_key_version:
                _ = await session.execute(
                    update_statement,
                    {
                        "account_id": account_id,
                        "target_key_version": state.target_key_version,
                    },
                )
            new_last = max(state.last_rotated_account_id, account_id)
            if new_last != state.last_rotated_account_id:
                _ = await session.execute(
                    progress_statement,
                    {"account_id": new_last},
                )
            await session.commit()

    async def complete_if_finished(self) -> bool:
        """Mark rotation complete only when all rows are at target version."""
        state = await self.get_state()
        if state is None:
            raise KeyRotationStateMissingError.default()
        if state.completed_at is not None:
            return True

        pending_statement = text(
            """
            SELECT 1
            FROM telegram_accounts
            WHERE key_version < :target_key_version
            LIMIT 1
            """,
        )
        complete_statement = text(
            """
            UPDATE auth_key_rotation
            SET completed_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = 1
              AND completed_at IS NULL
            """,
        )
        async with self._write_session_factory() as session:
            result = await session.execute(
                pending_statement,
                {"target_key_version": state.target_key_version},
            )
            pending = result.mappings().one_or_none()
            if pending is not None:
                await session.rollback()
                return False
            _ = await session.execute(complete_statement)
            await session.commit()
        return True


def _coerce_int(*, value: object, field_name: str) -> int:
    if isinstance(value, int):
        return value
    msg = f"Expected integer for {field_name}."
    raise KeyRotationError(msg)


def _validate_target_version(*, target_key_version: int) -> None:
    if target_key_version < 1:
        msg = "Key rotation target version must be >= 1."
        raise KeyRotationError(msg)
