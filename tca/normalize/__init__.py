"""Normalization module for TCA."""

from .hash_normalization import build_hash_normalized_input, normalize_hash_text
from .service import ItemWriteRepository, upsert_item
from .similarity_normalization import (
    build_similarity_normalized_input,
    normalize_similarity_text,
)
from .url_canonicalization import canonicalize_url

__all__ = [
    "ItemWriteRepository",
    "build_hash_normalized_input",
    "build_similarity_normalized_input",
    "canonicalize_url",
    "normalize_hash_text",
    "normalize_similarity_text",
    "upsert_item",
]
