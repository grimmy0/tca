"""Unit tests for exact URL dedupe strategy behavior."""

from __future__ import annotations

from tca.dedupe import (
    EXACT_URL_MATCH_REASON,
    EXACT_URL_MISMATCH_REASON,
    EXACT_URL_MISSING_REASON,
    evaluate_exact_url,
)


def test_equivalent_urls_return_duplicate() -> None:
    """Equivalent canonical URL hashes should return DUPLICATE."""
    result = evaluate_exact_url(
        left_canonical_url="https://example.com/post?a=1&b=2",
        right_canonical_url="https://example.com/post?a=1&b=2",
        left_canonical_url_hash="abc123",
        right_canonical_url_hash="abc123",
    )

    if result["status"] != "DUPLICATE":
        raise AssertionError
    if result["reason"] != EXACT_URL_MATCH_REASON:
        raise AssertionError


def test_non_equivalent_urls_return_distinct() -> None:
    """Different canonical URL values should return DISTINCT."""
    result = evaluate_exact_url(
        left_canonical_url="https://example.com/post-a",
        right_canonical_url="https://example.com/post-b",
        left_canonical_url_hash=None,
        right_canonical_url_hash=None,
    )

    if result["status"] != "DISTINCT":
        raise AssertionError
    if result["reason"] != EXACT_URL_MISMATCH_REASON:
        raise AssertionError


def test_missing_url_data_returns_abstain_with_reason_code() -> None:
    """Missing canonical URL/hash data should ABSTAIN with explicit reason code."""
    result = evaluate_exact_url(
        left_canonical_url=None,
        right_canonical_url="https://example.com/post",
        left_canonical_url_hash=None,
        right_canonical_url_hash=None,
    )

    if result["status"] != "ABSTAIN":
        raise AssertionError
    if result["reason"] != EXACT_URL_MISSING_REASON:
        raise AssertionError
