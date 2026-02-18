"""Deduplication module for TCA."""

from .content_hash_strategy import (
    CONTENT_HASH_MATCH_REASON,
    CONTENT_HASH_MISMATCH_REASON,
    CONTENT_HASH_MISSING_REASON,
    evaluate_content_hash,
)
from .exact_url_strategy import (
    EXACT_URL_MATCH_REASON,
    EXACT_URL_MISMATCH_REASON,
    EXACT_URL_MISSING_REASON,
    evaluate_exact_url,
)
from .strategy_contract import (
    STRATEGY_STATUSES,
    AbstainResult,
    DistinctResult,
    DuplicateResult,
    StrategyCallable,
    StrategyContractError,
    StrategyMetadata,
    StrategyResult,
    StrategyStatus,
    abstain,
    coerce_strategy_result,
    distinct,
    duplicate,
    run_strategy,
)

__all__ = [
    "CONTENT_HASH_MATCH_REASON",
    "CONTENT_HASH_MISMATCH_REASON",
    "CONTENT_HASH_MISSING_REASON",
    "EXACT_URL_MATCH_REASON",
    "EXACT_URL_MISMATCH_REASON",
    "EXACT_URL_MISSING_REASON",
    "STRATEGY_STATUSES",
    "AbstainResult",
    "DistinctResult",
    "DuplicateResult",
    "StrategyCallable",
    "StrategyContractError",
    "StrategyMetadata",
    "StrategyResult",
    "StrategyStatus",
    "abstain",
    "coerce_strategy_result",
    "distinct",
    "duplicate",
    "evaluate_content_hash",
    "evaluate_exact_url",
    "run_strategy",
]
