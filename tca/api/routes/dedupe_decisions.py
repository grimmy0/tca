"""Read-only dedupe decision trace route for explainability drill-down."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import cast

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import text

from tca.storage import DedupeDecisionRecord, DedupeDecisionsRepository, StorageRuntime

router = APIRouter()


class DedupeDecisionTraceEntryResponse(BaseModel):
    """One strategy-attempt decision record for an item dedupe trace."""

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


class DedupeDecisionTraceResponse(BaseModel):
    """Dedupe decision trace payload for one item id."""

    item_id: int
    decisions: list[DedupeDecisionTraceEntryResponse]


@router.get(
    "/dedupe/decisions/{item_id}",
    tags=["dedupe"],
    response_model=DedupeDecisionTraceResponse,
)
async def get_dedupe_decisions_trace(
    item_id: int,
    request: Request,
) -> DedupeDecisionTraceResponse:
    """Return decision-attempt trace rows for a specific item id."""
    runtime = _resolve_storage_runtime(request)
    if not await _item_exists(runtime=runtime, item_id=item_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Item '{item_id}' was not found.",
        )

    repository = _build_dedupe_decisions_repository(runtime=runtime)
    records = await repository.list_for_item(item_id=item_id)
    return DedupeDecisionTraceResponse(
        item_id=item_id,
        decisions=[_to_trace_entry_response(record=record) for record in records],
    )


async def _item_exists(*, runtime: StorageRuntime, item_id: int) -> bool:
    """Return true when the target item id exists in storage."""
    statement = text(
        """
        SELECT id
        FROM items
        WHERE id = :item_id
        LIMIT 1
        """,
    )
    async with runtime.read_session_factory() as session:
        row = (await session.execute(statement, {"item_id": item_id})).first()
    return row is not None


def _build_dedupe_decisions_repository(
    *,
    runtime: StorageRuntime,
) -> DedupeDecisionsRepository:
    """Create dedupe decisions repository bound to app runtime sessions."""
    return DedupeDecisionsRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
    )


def _to_trace_entry_response(
    *,
    record: DedupeDecisionRecord,
) -> DedupeDecisionTraceEntryResponse:
    """Map repository decision row to API response schema."""
    return DedupeDecisionTraceEntryResponse(
        decision_id=record.decision_id,
        item_id=record.item_id,
        cluster_id=record.cluster_id,
        candidate_item_id=record.candidate_item_id,
        strategy_name=record.strategy_name,
        outcome=record.outcome,
        reason_code=record.reason_code,
        score=record.score,
        metadata_json=record.metadata_json,
        created_at=record.created_at,
    )


def _resolve_storage_runtime(request: Request) -> StorageRuntime:
    """Load app storage runtime from FastAPI state with explicit failure mode."""
    state_obj = _resolve_app_state(request)
    runtime_obj = getattr(state_obj, "storage_runtime", None)
    if not isinstance(runtime_obj, StorageRuntime):
        message = "Missing app storage runtime: app.state.storage_runtime."
        raise TypeError(message)
    return runtime_obj


def _resolve_app_state(request: Request) -> object:
    """Resolve request app state with explicit object typing for static analysis."""
    request_obj = cast("object", request)
    app_obj = cast("object", getattr(request_obj, "app", None))
    return cast("object", getattr(app_obj, "state", None))
