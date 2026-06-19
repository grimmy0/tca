"""Unit tests for embedding service serialization and text preparation."""

from __future__ import annotations

import numpy as np

from tca.dedupe.embedding_service import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_DTYPE,
    embedding_to_array,
    prepare_text_for_embedding,
)


def test_embedding_to_array_roundtrip() -> None:
    """Serialized embedding bytes should roundtrip through embedding_to_array."""
    rng = np.random.default_rng(42)
    original = rng.random(EMBEDDING_DIMENSIONS).astype(EMBEDDING_DTYPE)
    blob = original.tobytes()
    restored = embedding_to_array(blob)

    if not np.array_equal(original, restored):
        raise AssertionError


def test_embedding_to_array_returns_writable_copy() -> None:
    """Returned array should be writable (not a read-only buffer view)."""
    blob = np.zeros(EMBEDDING_DIMENSIONS, dtype=EMBEDDING_DTYPE).tobytes()
    array = embedding_to_array(blob)
    array[0] = 1.0


def test_prepare_text_prepends_query_prefix() -> None:
    """Prepared text should start with the E5 query prefix."""
    result = prepare_text_for_embedding(title="test title", body="test body")

    if not result.startswith("query: "):
        raise AssertionError


def test_prepare_text_handles_none_inputs() -> None:
    """None title and body should still produce a valid prefixed string."""
    result = prepare_text_for_embedding(title=None, body=None)

    if not result.startswith("query: "):
        raise AssertionError
