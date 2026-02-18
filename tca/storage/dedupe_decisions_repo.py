"""Repository helpers for dedupe decision explainability records."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from json import dumps
from typing import TYPE_CHECKING, cast

from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from typing import Protocol

    from tca.storage.db import SessionFactory

    class _DecisionAttempt(Protocol):
        strategy_name: str
        outcome: str
        reason: str
        score: float | None


@dataclass(slots=True, frozen=True)
class DedupeDecisionRecord:
    """Typed dedupe decision row payload returned by read helpers."""

    decision_id: int
    item_id: int
    cluster_id: int | None
    candidate_item_id: int | None
    strategy_name: str
    outcome: str
    reason_code: str | None
    score: float | None
    metadata_json: str | None
    created_at: datetime


class DedupeDecisionsRepositoryError(RuntimeError):
    """Base exception for dedupe decisions repository operations."""


class DedupeDecisionsRepository:
    """Persistence flow for dedupe strategy decision attempts."""

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

    async def persist_attempts(
        self,
        *,
        item_id: int,
        cluster_id: int | None,
        candidate_item_id: int | None,
        decision_attempts: Sequence[_DecisionAttempt],
        metadata: Mapping[str, object] | None = None,
    ) -> tuple[int, ...]:
        """Persist each strategy decision attempt as an explainability row."""
        item_id = _coerce_write_int(value=item_id, field="item_id")
        cluster_id = _coerce_optional_write_int(value=cluster_id, field="cluster_id")
        candidate_item_id = _coerce_optional_write_int(
            value=candidate_item_id,
            field="candidate_item_id",
        )
        validated_attempts = _validate_attempts(decision_attempts=decision_attempts)
        if not validated_attempts:
            return ()

        insert_statement = text(
            """
            INSERT INTO dedupe_decisions (
                item_id,
                cluster_id,
                candidate_item_id,
                strategy_name,
                outcome,
                reason_code,
                score,
                metadata_json
            )
            VALUES (
                :item_id,
                :cluster_id,
                :candidate_item_id,
                :strategy_name,
                :outcome,
                :reason_code,
                :score,
                :metadata_json
            )
            RETURNING id
            """,
        )
        metadata_json = _encode_metadata(metadata=metadata)
        inserted_ids: list[int] = []

        async with self._write_session_factory() as session:
            for attempt in validated_attempts:
                inserted_row = (
                    (
                        await session.execute(
                            insert_statement,
                            {
                                "item_id": item_id,
                                "cluster_id": cluster_id,
                                "candidate_item_id": candidate_item_id,
                                "strategy_name": attempt.strategy_name,
                                "outcome": attempt.outcome,
                                "reason_code": attempt.reason,
                                "score": attempt.score,
                                "metadata_json": metadata_json,
                            },
                        )
                    )
                    .mappings()
                    .one()
                )
                inserted_ids.append(
                    _coerce_int(
                        value=inserted_row.get("id"),
                        field="id",
                    ),
                )
            await session.commit()

        return tuple(inserted_ids)

    async def list_for_item(self, *, item_id: int) -> tuple[DedupeDecisionRecord, ...]:
        """Return dedupe decision records for an item in insertion order."""
        statement = text(
            """
            SELECT
                id,
                item_id,
                cluster_id,
                candidate_item_id,
                strategy_name,
                outcome,
                reason_code,
                score,
                metadata_json,
                created_at
            FROM dedupe_decisions
            WHERE item_id = :item_id
            ORDER BY id ASC
            """,
        )
        async with self._read_session_factory() as session:
            rows = (
                (await session.execute(statement, {"item_id": item_id}))
                .mappings()
                .all()
            )
        return tuple(_decode_decision_row(row=row) for row in rows)

    async def list_for_cluster(
        self,
        *,
        cluster_id: int,
    ) -> tuple[DedupeDecisionRecord, ...]:
        """Return dedupe decision records for a cluster in insertion order."""
        statement = text(
            """
            SELECT
                id,
                item_id,
                cluster_id,
                candidate_item_id,
                strategy_name,
                outcome,
                reason_code,
                score,
                metadata_json,
                created_at
            FROM dedupe_decisions
            WHERE cluster_id = :cluster_id
            ORDER BY id ASC
            """,
        )
        async with self._read_session_factory() as session:
            rows = (
                (await session.execute(statement, {"cluster_id": cluster_id}))
                .mappings()
                .all()
            )
        return tuple(_decode_decision_row(row=row) for row in rows)


def _decode_decision_row(*, row: object) -> DedupeDecisionRecord:
    row_map = cast("dict[str, object]", row)
    return DedupeDecisionRecord(
        decision_id=_coerce_int(value=row_map.get("id"), field="id"),
        item_id=_coerce_int(value=row_map.get("item_id"), field="item_id"),
        cluster_id=_coerce_optional_int(
            value=row_map.get("cluster_id"),
            field="cluster_id",
        ),
        candidate_item_id=_coerce_optional_int(
            value=row_map.get("candidate_item_id"),
            field="candidate_item_id",
        ),
        strategy_name=_coerce_str(
            value=row_map.get("strategy_name"),
            field="strategy_name",
        ),
        outcome=_coerce_str(value=row_map.get("outcome"), field="outcome"),
        reason_code=_coerce_optional_str(value=row_map.get("reason_code")),
        score=_coerce_optional_float(value=row_map.get("score"), field="score"),
        metadata_json=_coerce_optional_str(value=row_map.get("metadata_json")),
        created_at=_coerce_datetime(
            value=row_map.get("created_at"),
            field="created_at",
        ),
    )


def _coerce_int(*, value: object, field: str) -> int:
    if isinstance(value, bool):
        msg = f"missing integer `{field}`"
        raise DedupeDecisionsRepositoryError(msg)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    msg = f"missing integer `{field}`"
    raise DedupeDecisionsRepositoryError(msg)


def _coerce_write_int(*, value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"invalid `{field}` value"
        raise DedupeDecisionsRepositoryError(msg)
    return value


def _coerce_optional_write_int(*, value: object, field: str) -> int | None:
    if value is None:
        return None
    return _coerce_write_int(value=value, field=field)


def _coerce_non_empty_str(*, value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        msg = f"invalid `{field}` value"
        raise DedupeDecisionsRepositoryError(msg)
    return value


def _coerce_optional_finite_float(*, value: object, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = f"invalid `{field}` value"
        raise DedupeDecisionsRepositoryError(msg)
    score = float(value)
    if not math.isfinite(score):
        msg = f"invalid `{field}` value"
        raise DedupeDecisionsRepositoryError(msg)
    return score


def _validate_attempts(
    *,
    decision_attempts: Sequence[_DecisionAttempt],
) -> tuple[_DecisionAttempt, ...]:
    validated: list[_DecisionAttempt] = []
    for index, attempt in enumerate(decision_attempts):
        _ = _coerce_non_empty_str(
            value=attempt.strategy_name,
            field=f"decision_attempts[{index}].strategy_name",
        )
        _ = _coerce_non_empty_str(
            value=attempt.outcome,
            field=f"decision_attempts[{index}].outcome",
        )
        _ = _coerce_non_empty_str(
            value=attempt.reason,
            field=f"decision_attempts[{index}].reason",
        )
        _ = _coerce_optional_finite_float(
            value=attempt.score,
            field=f"decision_attempts[{index}].score",
        )
        validated.append(attempt)
    return tuple(validated)


def _coerce_optional_int(*, value: object, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        msg = f"invalid `{field}` value"
        raise DedupeDecisionsRepositoryError(msg)
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    msg = f"invalid `{field}` value"
    raise DedupeDecisionsRepositoryError(msg)


def _coerce_str(*, value: object, field: str) -> str:
    if isinstance(value, str):
        return value
    msg = f"missing `{field}`"
    raise DedupeDecisionsRepositoryError(msg)


def _coerce_optional_str(*, value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    msg = "invalid string value"
    raise DedupeDecisionsRepositoryError(msg)


def _coerce_optional_float(*, value: object, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        msg = f"invalid `{field}` value"
        raise DedupeDecisionsRepositoryError(msg)
    if isinstance(value, int | float):
        float_value = float(value)
        if not math.isfinite(float_value):
            msg = f"invalid `{field}` value"
            raise DedupeDecisionsRepositoryError(msg)
        return float_value
    if isinstance(value, str):
        try:
            float_value = float(value)
        except ValueError as exc:
            msg = f"invalid `{field}` value"
            raise DedupeDecisionsRepositoryError(msg) from exc
        if not math.isfinite(float_value):
            msg = f"invalid `{field}` value"
            raise DedupeDecisionsRepositoryError(msg)
        return float_value
    msg = f"invalid `{field}` value"
    raise DedupeDecisionsRepositoryError(msg)


def _coerce_datetime(*, value: object, field: str) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return _parse_datetime(value, field=field)
    msg = f"missing `{field}`"
    raise DedupeDecisionsRepositoryError(msg)


def _parse_datetime(value: str, *, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        msg = f"invalid `{field}` value"
        raise DedupeDecisionsRepositoryError(msg) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _encode_metadata(*, metadata: Mapping[str, object] | None) -> str | None:
    if metadata is None:
        return None
    try:
        return dumps(
            dict(metadata),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        msg = "invalid `metadata` value"
        raise DedupeDecisionsRepositoryError(msg) from exc
