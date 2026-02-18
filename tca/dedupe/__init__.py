"""Deduplication module for TCA."""

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
    StrategyResult,
    StrategyStatus,
    abstain,
    coerce_strategy_result,
    distinct,
    duplicate,
    run_strategy,
)

__all__ = [
    "EXACT_URL_MATCH_REASON",
    "EXACT_URL_MISMATCH_REASON",
    "EXACT_URL_MISSING_REASON",
    "STRATEGY_STATUSES",
    "AbstainResult",
    "DistinctResult",
    "DuplicateResult",
    "StrategyCallable",
    "StrategyContractError",
    "StrategyResult",
    "StrategyStatus",
    "abstain",
    "coerce_strategy_result",
    "distinct",
    "duplicate",
    "evaluate_exact_url",
    "run_strategy",
]
