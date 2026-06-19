"""Unit tests for semantic similarity dedupe strategy behavior."""

from __future__ import annotations

import numpy as np

from tca.dedupe import (
    SEMANTIC_SIMILARITY_INVALID_REASON,
    SEMANTIC_SIMILARITY_MATCH_REASON,
    SEMANTIC_SIMILARITY_MISMATCH_REASON,
    SEMANTIC_SIMILARITY_MISSING_REASON,
    evaluate_semantic_similarity,
)
from tca.dedupe.embedding_service import EMBEDDING_DIMENSIONS, EMBEDDING_DTYPE


def _make_embedding(vector: list[float]) -> bytes:
    return np.array(vector, dtype=EMBEDDING_DTYPE).tobytes()


def _unit_vector(dimension: int = 0) -> bytes:
    vector = np.zeros(EMBEDDING_DIMENSIONS, dtype=EMBEDDING_DTYPE)
    vector[dimension] = 1.0
    return vector.tobytes()


def test_identical_embeddings_return_duplicate() -> None:
    """Same vector should return DUPLICATE with score ~1.0."""
    embedding = _unit_vector(0)
    result = evaluate_semantic_similarity(
        left_embedding=embedding,
        right_embedding=embedding,
    )

    if result["status"] != "DUPLICATE":
        raise AssertionError
    if result["reason"] != SEMANTIC_SIMILARITY_MATCH_REASON:
        raise AssertionError


def test_dissimilar_embeddings_return_distinct() -> None:
    """Orthogonal vectors should return DISTINCT."""
    result = evaluate_semantic_similarity(
        left_embedding=_unit_vector(0),
        right_embedding=_unit_vector(1),
    )

    if result["status"] != "DISTINCT":
        raise AssertionError
    if result["reason"] != SEMANTIC_SIMILARITY_MISMATCH_REASON:
        raise AssertionError


def test_missing_left_embedding_abstains() -> None:
    """None left embedding should ABSTAIN."""
    result = evaluate_semantic_similarity(
        left_embedding=None,
        right_embedding=_unit_vector(0),
    )

    if result["status"] != "ABSTAIN":
        raise AssertionError
    if result["reason"] != SEMANTIC_SIMILARITY_MISSING_REASON:
        raise AssertionError


def test_missing_right_embedding_abstains() -> None:
    """None right embedding should ABSTAIN."""
    result = evaluate_semantic_similarity(
        left_embedding=_unit_vector(0),
        right_embedding=None,
    )

    if result["status"] != "ABSTAIN":
        raise AssertionError
    if result["reason"] != SEMANTIC_SIMILARITY_MISSING_REASON:
        raise AssertionError


def test_wrong_dimension_embedding_abstains() -> None:
    """Truncated embedding bytes should ABSTAIN with invalid reason."""
    truncated = _unit_vector(0)[:100]
    result = evaluate_semantic_similarity(
        left_embedding=truncated,
        right_embedding=_unit_vector(0),
    )

    if result["status"] != "ABSTAIN":
        raise AssertionError
    if result["reason"] != SEMANTIC_SIMILARITY_INVALID_REASON:
        raise AssertionError


def test_custom_threshold_controls_boundary() -> None:
    """Threshold above 1.0 should make even identical vectors DISTINCT."""
    embedding = _unit_vector(0)
    result = evaluate_semantic_similarity(
        left_embedding=embedding,
        right_embedding=embedding,
        threshold=1.01,
    )

    if result["status"] != "DISTINCT":
        raise AssertionError


def test_metadata_includes_score_and_threshold() -> None:
    """Result metadata should contain score and threshold values."""
    embedding = _unit_vector(0)
    result = evaluate_semantic_similarity(
        left_embedding=embedding,
        right_embedding=embedding,
    )

    metadata = result.get("metadata")
    if metadata is None:
        raise AssertionError
    if "score" not in metadata:
        raise AssertionError
    if "threshold" not in metadata:
        raise AssertionError


_EPSILON = 1e-5


def test_unnormalized_vectors_are_normalized() -> None:
    """Vectors that are not unit length should be normalized during evaluation."""
    # Create two parallel vectors with different lengths (e.g. 2.0 and 0.5)
    v1 = np.zeros(EMBEDDING_DIMENSIONS, dtype=EMBEDDING_DTYPE)
    v1[0] = 2.0
    v2 = np.zeros(EMBEDDING_DIMENSIONS, dtype=EMBEDDING_DTYPE)
    v2[0] = 0.5

    result = evaluate_semantic_similarity(
        left_embedding=v1.tobytes(),
        right_embedding=v2.tobytes(),
        threshold=0.99,
    )

    if result["status"] != "DUPLICATE":
        raise AssertionError
    metadata = result.get("metadata")
    if metadata is None:
        raise AssertionError
    # The score should be ~1.0 because both are normalized to [1.0, 0.0, ...]
    if abs(metadata.get("score", 0.0) - 1.0) > _EPSILON:
        raise AssertionError
