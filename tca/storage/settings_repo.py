"""Repository helpers for `settings` table CRUD and JSON value conversion."""

from __future__ import annotations

import json
from dataclasses import dataclass
from json import JSONDecodeError
from typing import TYPE_CHECKING, cast

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

if TYPE_CHECKING:
    from tca.storage.db import SessionFactory

type JSONScalar = str | int | float | bool | None
type JSONValue = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]


@dataclass(slots=True, frozen=True)
class SettingRecord:
    """Typed settings row payload resolved from JSON storage."""

    key: str
    value: JSONValue


class SettingsRepositoryError(RuntimeError):
    """Base exception for settings repository operations."""


class SettingAlreadyExistsError(SettingsRepositoryError):
    """Raised when inserting a setting key that already exists."""

    @classmethod
    def for_key(cls, key: str) -> SettingAlreadyExistsError:
        """Build deterministic duplicate-key error for repository callers."""
        message = f"Setting already exists for key '{key}'."
        return cls(message)


class SettingValueEncodeError(SettingsRepositoryError):
    """Raised when a setting value cannot be encoded into JSON."""

    @classmethod
    def for_key(
        cls,
        key: str,
        *,
        details: str,
    ) -> SettingValueEncodeError:
        """Build deterministic encode error with key-localized context."""
        message = f"Setting value for key '{key}' is not JSON-serializable: {details}"
        return cls(message)


class SettingValueDecodeError(SettingsRepositoryError):
    """Raised when value_json cannot be decoded into expected JSON value."""

    @classmethod
    def for_key(
        cls,
        key: str,
        *,
        details: str,
    ) -> SettingValueDecodeError:
        """Build deterministic decode error with key-localized context."""
        message = (
            f"Stored setting value for key '{key}' is not valid JSON payload: {details}"
        )
        return cls(message)


class SettingsRepository:
    """CRUD helper for dynamic settings rows keyed by `settings.key`."""

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

    async def create(self, *, key: str, value: JSONValue) -> SettingRecord:
        """Insert a new setting row; fail deterministically on duplicate key."""
        encoded_value = _encode_value_json(key=key, value=value)
        statement = text(
            """
            INSERT INTO settings (key, value_json)
            VALUES (:key, :value_json)
            RETURNING key, value_json
            """,
        )
        async with self._write_session_factory() as session:
            try:
                result = await session.execute(
                    statement,
                    {"key": key, "value_json": encoded_value},
                )
                row = result.mappings().one()
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise SettingAlreadyExistsError.for_key(key) from exc
        return _decode_row(row)

    async def get_by_key(self, *, key: str) -> SettingRecord | None:
        """Fetch a setting by key or return None when it does not exist."""
        statement = text(
            """
            SELECT key, value_json
            FROM settings
            WHERE key = :key
            """,
        )
        async with self._read_session_factory() as session:
            result = await session.execute(statement, {"key": key})
            row = result.mappings().one_or_none()
        if row is None:
            return None
        return _decode_row(row)

    async def update(self, *, key: str, value: JSONValue) -> SettingRecord | None:
        """Update an existing key and return row, or None if missing."""
        encoded_value = _encode_value_json(key=key, value=value)
        statement = text(
            """
            UPDATE settings
            SET value_json = :value_json,
                updated_at = CURRENT_TIMESTAMP
            WHERE key = :key
            RETURNING key, value_json
            """,
        )
        async with self._write_session_factory() as session:
            result = await session.execute(
                statement,
                {"key": key, "value_json": encoded_value},
            )
            row = result.mappings().one_or_none()
            await session.commit()
        if row is None:
            return None
        return _decode_row(row)


def _decode_row(row: object) -> SettingRecord:
    """Decode a row mapping into SettingRecord with JSON fidelity checks."""
    row_map = cast("dict[str, object]", row)
    key_obj = row_map.get("key")
    value_json_obj = row_map.get("value_json")
    if not isinstance(key_obj, str):
        unknown_key = "<unknown>"
        missing_key_details = "missing `key`."
        raise SettingValueDecodeError.for_key(unknown_key, details=missing_key_details)
    if not isinstance(value_json_obj, str):
        missing_value_details = "missing `value_json` text."
        raise SettingValueDecodeError.for_key(
            key_obj,
            details=missing_value_details,
        )
    decoded = _decode_value_json(key=key_obj, value_json=value_json_obj)
    return SettingRecord(key=key_obj, value=decoded)


def _encode_value_json(*, key: str, value: JSONValue) -> str:
    """Serialize setting value to compact JSON text."""
    try:
        return json.dumps(
            value,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise SettingValueEncodeError.for_key(key, details=str(exc)) from exc


def _decode_value_json(*, key: str, value_json: str) -> JSONValue:
    """Deserialize JSON text and validate value remains JSON-compatible."""
    try:
        decoded = cast("object", json.loads(value_json))
    except JSONDecodeError as exc:
        raise SettingValueDecodeError.for_key(key, details=str(exc)) from exc
    if not _is_json_value(decoded):
        raise SettingValueDecodeError.for_key(
            key,
            details=f"unsupported decoded type {type(decoded)!r}",
        )
    return cast("JSONValue", decoded)


def _is_json_value(value: object) -> bool:
    """Recursively verify decoded value belongs to JSON type domain."""
    if value is None:
        return True
    if isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        items = cast("list[object]", value)
        return all(_is_json_value(item) for item in items)
    if isinstance(value, dict):
        entries = cast("dict[object, object]", value)
        return all(
            isinstance(key, str) and _is_json_value(val) for key, val in entries.items()
        )
    return False
