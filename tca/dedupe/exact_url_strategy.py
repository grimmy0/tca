"""Exact URL dedupe strategy based on canonical URL hash/value equality."""

from __future__ import annotations

from .strategy_contract import StrategyResult, abstain, distinct, duplicate

EXACT_URL_MATCH_REASON = "exact_url_match"
EXACT_URL_MISMATCH_REASON = "exact_url_mismatch"
EXACT_URL_MISSING_REASON = "exact_url_missing"


def evaluate_exact_url(
    *,
    left_canonical_url: str | None,
    right_canonical_url: str | None,
    left_canonical_url_hash: str | None,
    right_canonical_url_hash: str | None,
) -> StrategyResult:
    """Compare two items by canonical URL hash/value and return strategy result."""
    if left_canonical_url_hash is not None and right_canonical_url_hash is not None:
        if left_canonical_url_hash == right_canonical_url_hash:
            return duplicate(score=1.0, reason=EXACT_URL_MATCH_REASON)
        return distinct(reason=EXACT_URL_MISMATCH_REASON)

    if left_canonical_url is not None and right_canonical_url is not None:
        if left_canonical_url == right_canonical_url:
            return duplicate(score=1.0, reason=EXACT_URL_MATCH_REASON)
        return distinct(reason=EXACT_URL_MISMATCH_REASON)

    return abstain(reason=EXACT_URL_MISSING_REASON)
