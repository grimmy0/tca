"""Ingestion module for TCA."""

from .service import fetch_recent_messages, upsert_raw_message

__all__ = ["fetch_recent_messages", "upsert_raw_message"]
