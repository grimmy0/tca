"""Ingestion module for TCA."""

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
    "upsert_raw_message",
]
