"""Unit tests for title similarity dedupe strategy behavior."""

from __future__ import annotations

from tca.dedupe import (
    TITLE_SIMILARITY_MATCH_REASON,
    TITLE_SIMILARITY_MISMATCH_REASON,
    TITLE_SIMILARITY_SHORT_TITLE_REASON,
    evaluate_title_similarity,
)


def test_above_threshold_pair_returns_duplicate() -> None:
    """Highly similar normalized token sets should return DUPLICATE."""
    result = evaluate_title_similarity(
        left_title="Breaking major earthquake update in city center now",
        right_title="city center now update breaking major earthquake in",
    )

    if result["status"] != "DUPLICATE":
        raise AssertionError
    if result["reason"] != TITLE_SIMILARITY_MATCH_REASON:
        raise AssertionError


def test_below_threshold_pair_returns_non_duplicate_decision() -> None:
    """Dissimilar normalized titles should return a non-duplicate decision."""
    result = evaluate_title_similarity(
        left_title="Local weather forecast predicts rain this afternoon",
        right_title="Stock market closes higher after tech rally",
    )

    if result["status"] == "DUPLICATE":
        raise AssertionError
    if result["reason"] != TITLE_SIMILARITY_MISMATCH_REASON:
        raise AssertionError


def test_short_title_cases_return_abstain() -> None:
    """Titles below the minimum token count guard should ABSTAIN."""
    result = evaluate_title_similarity(
        left_title="quick note",
        right_title="quick note update",
    )

    if result["status"] != "ABSTAIN":
        raise AssertionError
    if result["reason"] != TITLE_SIMILARITY_SHORT_TITLE_REASON:
        raise AssertionError
