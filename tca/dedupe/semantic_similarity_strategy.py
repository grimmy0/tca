"""Semantic similarity dedupe strategy using precomputed embeddings."""

from __future__ import annotations

import numpy as np

from .embedding_service import EMBEDDING_DIMENSIONS, EMBEDDING_DTYPE, embedding_to_array
from .strategy_contract import StrategyResult, abstain, distinct, duplicate

SEMANTIC_SIMILARITY_THRESHOLD_DEFAULT = 0.85
SEMANTIC_SIMILARITY_MATCH_REASON = "semantic_similarity_match"
SEMANTIC_SIMILARITY_MISMATCH_REASON = "semantic_similarity_mismatch"
SEMANTIC_SIMILARITY_MISSING_REASON = "semantic_similarity_missing"
SEMANTIC_SIMILARITY_INVALID_REASON = "semantic_similarity_invalid"

_EXPECTED_BYTE_LENGTH = EMBEDDING_DIMENSIONS * np.dtype(EMBEDDING_DTYPE).itemsize


def evaluate_semantic_similarity(
    *,
    left_embedding: bytes | None,
    right_embedding: bytes | None,
    threshold: float = SEMANTIC_SIMILARITY_THRESHOLD_DEFAULT,
) -> StrategyResult:
    """Compare two precomputed embedding BLOBs via cosine similarity."""
    if left_embedding is None or right_embedding is None:
        return abstain(
            reason=SEMANTIC_SIMILARITY_MISSING_REASON,
            metadata={"threshold": threshold},
        )

    if (
        len(left_embedding) != _EXPECTED_BYTE_LENGTH
        or len(right_embedding) != _EXPECTED_BYTE_LENGTH
    ):
        return abstain(
            reason=SEMANTIC_SIMILARITY_INVALID_REASON,
            metadata={"threshold": threshold},
        )

    left_array = embedding_to_array(left_embedding)
    right_array = embedding_to_array(right_embedding)

    norm_left = np.linalg.norm(left_array)
    if norm_left > 0:
        left_array = left_array / norm_left

    norm_right = np.linalg.norm(right_array)
    if norm_right > 0:
        right_array = right_array / norm_right

    dot_result = np.dot(left_array, right_array)  # pyright: ignore[reportAny]
    score = float(dot_result)  # pyright: ignore[reportAny]
    metadata = {"score": score, "threshold": threshold}

    if score >= threshold:
        return duplicate(
            score=score,
            reason=SEMANTIC_SIMILARITY_MATCH_REASON,
            metadata=metadata,
        )
    return distinct(reason=SEMANTIC_SIMILARITY_MISMATCH_REASON, metadata=metadata)
