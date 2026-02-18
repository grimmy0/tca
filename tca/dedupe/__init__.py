"""Deduplication module for TCA."""

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
    "run_strategy",
]
