"""Ordered dedupe strategy-chain execution with short-circuit semantics."""

from __future__ import annotations

from collections.abc import Sequence

from .strategy_contract import StrategyCallable, StrategyResult, distinct, run_strategy

NO_STRATEGY_MATCH_REASON = "no_strategy_match"
StrategyChain = Sequence[tuple[str, StrategyCallable]]


def execute_strategy_chain(*, strategies: StrategyChain) -> StrategyResult:
    """Run strategies in order, returning first non-ABSTAIN outcome."""
    for strategy_name, strategy in strategies:
        result = run_strategy(strategy_name=strategy_name, strategy=strategy)
        if result["status"] == "ABSTAIN":
            continue
        return result
    return distinct(reason=NO_STRATEGY_MATCH_REASON)
