"""Title similarity dedupe strategy using RapidFuzz token_set_ratio."""

from __future__ import annotations

from rapidfuzz.fuzz import token_set_ratio

from tca.normalize import normalize_similarity_text

from .strategy_contract import StrategyResult, abstain, distinct, duplicate

TITLE_SIMILARITY_THRESHOLD_DEFAULT = 0.92
TITLE_SIMILARITY_MIN_TOKEN_COUNT = 3
TITLE_SIMILARITY_MATCH_REASON = "title_similarity_match"
TITLE_SIMILARITY_MISMATCH_REASON = "title_similarity_mismatch"
TITLE_SIMILARITY_SHORT_TITLE_REASON = "title_similarity_short_title"


def evaluate_title_similarity(
    *,
    left_title: str | None,
    right_title: str | None,
    threshold: float = TITLE_SIMILARITY_THRESHOLD_DEFAULT,
) -> StrategyResult:
    """Compare two normalized titles with token_set_ratio and threshold semantics."""
    left_normalized = normalize_similarity_text(left_title)
    right_normalized = normalize_similarity_text(right_title)

    left_token_count = _token_count(value=left_normalized)
    right_token_count = _token_count(value=right_normalized)
    metadata = {
        "left_token_count": left_token_count,
        "right_token_count": right_token_count,
        "threshold": threshold,
    }

    if (
        left_token_count < TITLE_SIMILARITY_MIN_TOKEN_COUNT
        or right_token_count < TITLE_SIMILARITY_MIN_TOKEN_COUNT
    ):
        return abstain(reason=TITLE_SIMILARITY_SHORT_TITLE_REASON, metadata=metadata)

    score = token_set_ratio(left_normalized, right_normalized) / 100.0
    metadata["score"] = score
    if score >= threshold:
        return duplicate(
            score=score,
            reason=TITLE_SIMILARITY_MATCH_REASON,
            metadata=metadata,
        )
    return distinct(reason=TITLE_SIMILARITY_MISMATCH_REASON, metadata=metadata)


def _token_count(*, value: str) -> int:
    if not value:
        return 0
    return len(value.split())
