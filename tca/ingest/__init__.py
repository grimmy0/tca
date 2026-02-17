"""Ingestion module for TCA."""

from .account_risk import record_account_risk_breach
from .flood_wait import handle_flood_wait
from .service import (
    BoundedPaginationResult,
    fetch_bounded_messages,
    fetch_recent_messages,
    upsert_raw_message,
)

__all__ = [
    "BoundedPaginationResult",
    "fetch_bounded_messages",
    "fetch_recent_messages",
    "handle_flood_wait",
    "record_account_risk_breach",
    "upsert_raw_message",
]
