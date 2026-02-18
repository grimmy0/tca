"""Content hash dedupe strategy based on normalized title/body hash equality."""

from __future__ import annotations

import hashlib

from tca.normalize import build_hash_normalized_input

from .strategy_contract import StrategyResult, abstain, distinct, duplicate

CONTENT_HASH_MATCH_REASON = "content_hash_match"
CONTENT_HASH_MISMATCH_REASON = "content_hash_mismatch"
CONTENT_HASH_MISSING_REASON = "content_hash_missing"


def evaluate_content_hash(
    *,
    left_title: str | None,
    left_body: str | None,
    right_title: str | None,
    right_body: str | None,
) -> StrategyResult:
    """Compare two items by hash over normalized title + body content."""
    left_normalized = build_hash_normalized_input(title=left_title, body=left_body)
    right_normalized = build_hash_normalized_input(title=right_title, body=right_body)

    left_hash = _sha256_hex(value=left_normalized) if left_normalized else None
    right_hash = _sha256_hex(value=right_normalized) if right_normalized else None
    metadata = {"left_content_hash": left_hash, "right_content_hash": right_hash}

    if left_hash is None or right_hash is None:
        return abstain(reason=CONTENT_HASH_MISSING_REASON, metadata=metadata)
    if left_hash == right_hash:
        return duplicate(score=1.0, reason=CONTENT_HASH_MATCH_REASON, metadata=metadata)
    return distinct(reason=CONTENT_HASH_MISMATCH_REASON, metadata=metadata)


def _sha256_hex(*, value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
