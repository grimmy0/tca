"""Normalization module for TCA."""

from .service import ItemWriteRepository, upsert_item
from .url_canonicalization import canonicalize_url

__all__ = [
    "ItemWriteRepository",
    "canonicalize_url",
    "upsert_item",
]
