"""Thread timeline API routes."""

from __future__ import annotations

from datetime import datetime  # noqa: TC003
from typing import Annotated, cast

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from tca.storage import StorageRuntime, ThreadEntryRecord, ThreadQueryRepository

router = APIRouter()


class ThreadRepresentativeResponse(BaseModel):
    """Representative item payload for one dedupe cluster."""

    item_id: int
    published_at: datetime | None
    title: str | None
    body: str | None
    canonical_url: str | None
    channel_id: int
    channel_name: str
    channel_username: str | None


class ThreadEntryResponse(BaseModel):
    """Cluster-level thread entry payload including duplicate count."""

    cluster_id: int
    cluster_key: str
    duplicate_count: int
    representative: ThreadRepresentativeResponse


@router.get("/thread", tags=["thread"], response_model=list[ThreadEntryResponse])
async def list_thread_entries(
    request: Request,
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[ThreadEntryResponse]:
    """List one page of deduplicated thread entries by representative recency."""
    repository = _build_thread_query_repository(request)
    records = await repository.list_entries(page=page, page_size=size)
    return [_to_thread_entry_response(record=record) for record in records]


def _to_thread_entry_response(*, record: ThreadEntryRecord) -> ThreadEntryResponse:
    """Map repository row payload to API response model."""
    return ThreadEntryResponse(
        cluster_id=record.cluster_id,
        cluster_key=record.cluster_key,
        duplicate_count=record.duplicate_count,
        representative=ThreadRepresentativeResponse(
            item_id=record.representative_item_id,
            published_at=record.representative_published_at,
            title=record.representative_title,
            body=record.representative_body,
            canonical_url=record.representative_canonical_url,
            channel_id=record.representative_channel_id,
            channel_name=record.representative_channel_name,
            channel_username=record.representative_channel_username,
        ),
    )


def _build_thread_query_repository(request: Request) -> ThreadQueryRepository:
    """Create thread repository bound to app runtime read/write sessions."""
    runtime = _resolve_storage_runtime(request)
    return ThreadQueryRepository(
        read_session_factory=runtime.read_session_factory,
        write_session_factory=runtime.write_session_factory,
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
