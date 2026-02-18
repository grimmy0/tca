"""Ordered dedupe strategy-chain execution with short-circuit semantics."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .strategy_contract import StrategyCallable, StrategyResult, distinct, run_strategy

NO_STRATEGY_MATCH_REASON = "no_strategy_match"
StrategyChain = Sequence[tuple[str, StrategyCallable]]


@dataclass(frozen=True, slots=True)
class StrategyDecisionAttempt:
    """One evaluated strategy attempt in configured execution order."""

    strategy_name: str
    outcome: str
    reason: str
    score: float | None


def execute_strategy_chain(*, strategies: StrategyChain) -> StrategyResult:
    """Run strategies in order, returning first non-ABSTAIN outcome."""
    result, _ = execute_strategy_chain_with_trace(strategies=strategies)
    return result


def execute_strategy_chain_with_trace(
    *,
    strategies: StrategyChain,
) -> tuple[StrategyResult, tuple[StrategyDecisionAttempt, ...]]:
    """Run strategies and return result plus per-attempt explainability trace."""
    attempts: list[StrategyDecisionAttempt] = []
    for strategy_name, strategy in strategies:
        result = run_strategy(strategy_name=strategy_name, strategy=strategy)
        score_obj = result.get("score")
        score: float | None = None
        if isinstance(score_obj, int | float) and not isinstance(score_obj, bool):
            score = float(score_obj)
        attempts.append(
            StrategyDecisionAttempt(
                strategy_name=strategy_name,
                outcome=result["status"],
                reason=result["reason"],
                score=score,
            ),
        )
        if result["status"] == "ABSTAIN":
            continue
        return result, tuple(attempts)

    return distinct(reason=NO_STRATEGY_MATCH_REASON), tuple(attempts)
