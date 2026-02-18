"""Unit tests for content hash dedupe strategy behavior."""

from __future__ import annotations

from tca.dedupe import (
    CONTENT_HASH_MATCH_REASON,
    CONTENT_HASH_MISMATCH_REASON,
    evaluate_content_hash,
)


def test_equal_normalized_content_returns_duplicate() -> None:
    """Semantically equivalent title/body content should return DUPLICATE."""
    result = evaluate_content_hash(
        left_title="\uff26\uff2f\uff2f BAR",
        left_body=(
            "Read https://t.me/iv?url=https%3A%2F%2FExample.com%2Fa%2Fb%3F"
            "utm_source%3Dtelegram%26x%3D1 now!"
        ),
        right_title="foo bar",
        right_body="Read https://example.com/a/b?x=1 now",
    )

    if result["status"] != "DUPLICATE":
        raise AssertionError
    if result["reason"] != CONTENT_HASH_MATCH_REASON:
        raise AssertionError


def test_different_normalized_content_does_not_return_duplicate() -> None:
    """Different normalized content should produce a non-duplicate result."""
    result = evaluate_content_hash(
        left_title="Alpha",
        left_body="Body one",
        right_title="Alpha",
        right_body="Body two",
    )

    if result["status"] == "DUPLICATE":
        raise AssertionError
    if result["reason"] != CONTENT_HASH_MISMATCH_REASON:
        raise AssertionError


def test_decision_metadata_includes_compared_hash_values() -> None:
    """Decision metadata should expose both compared content hash values."""
    result = evaluate_content_hash(
        left_title="Left title",
        left_body="Left body",
        right_title="Right title",
        right_body="Right body",
    )

    metadata = result.get("metadata")
    if not isinstance(metadata, dict):
        raise TypeError

    left_hash = metadata.get("left_content_hash")
    right_hash = metadata.get("right_content_hash")
    if not isinstance(left_hash, str):
        raise TypeError
    if not isinstance(right_hash, str):
        raise TypeError
    if not left_hash or not right_hash:
        raise AssertionError
