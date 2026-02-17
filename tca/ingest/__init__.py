"""Ingestion module for TCA."""

from .account_risk import record_account_risk_breach
from .error_capture import (
    ALLOWED_INGEST_ERROR_STAGES,
    IngestErrorStage,
    capture_ingest_error,
    execute_with_ingest_error_capture,
    normalize_ingest_error_stage,
)
from .flood_wait import handle_flood_wait
from .service import (
    BoundedPaginationResult,
    fetch_bounded_messages,
    fetch_recent_messages,
    upsert_raw_message,
)

__all__ = [
    "BoundedPaginationResult",
    "ALLOWED_INGEST_ERROR_STAGES",
    "IngestErrorStage",
    "capture_ingest_error",
    "execute_with_ingest_error_capture",
    "fetch_bounded_messages",
    "fetch_recent_messages",
    "handle_flood_wait",
    "normalize_ingest_error_stage",
    "record_account_risk_breach",
    "upsert_raw_message",
]
