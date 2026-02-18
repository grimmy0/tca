"""Unit tests for ordered strategy-chain execution semantics."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tca.dedupe import (
    NO_STRATEGY_MATCH_REASON,
    StrategyContractError,
    execute_strategy_chain,
)

if TYPE_CHECKING:
    from collections.abc import Callable


def test_first_duplicate_short_circuits_evaluation() -> None:
    """The first DUPLICATE decision should stop subsequent strategies."""
    executed: list[str] = []

    result = execute_strategy_chain(
        strategies=(
            ("first_abstain", _abstain_strategy("first_abstain", executed)),
            ("second_duplicate", _duplicate_strategy("second_duplicate", executed)),
            ("third_distinct", _distinct_strategy("third_distinct", executed)),
        ),
    )

    if result["status"] != "DUPLICATE":
        raise AssertionError
    if executed != ["first_abstain", "second_duplicate"]:
        raise AssertionError


def test_first_distinct_short_circuits_evaluation() -> None:
    """The first DISTINCT decision should stop subsequent strategies."""
    executed: list[str] = []

    result = execute_strategy_chain(
        strategies=(
            ("first_abstain", _abstain_strategy("first_abstain", executed)),
            ("second_distinct", _distinct_strategy("second_distinct", executed)),
            ("third_duplicate", _duplicate_strategy("third_duplicate", executed)),
        ),
    )

    if result["status"] != "DISTINCT":
        raise AssertionError
    if result["reason"] != "second_distinct_reason":
        raise AssertionError
    if executed != ["first_abstain", "second_distinct"]:
        raise AssertionError


def test_all_abstain_returns_distinct_no_strategy_match() -> None:
    """All-ABSTAIN chains should map to DISTINCT(no_strategy_match)."""
    executed: list[str] = []

    result = execute_strategy_chain(
        strategies=(
            ("first_abstain", _abstain_strategy("first_abstain", executed)),
            ("second_abstain", _abstain_strategy("second_abstain", executed)),
            ("third_abstain", _abstain_strategy("third_abstain", executed)),
        ),
    )

    if result["status"] != "DISTINCT":
        raise AssertionError
    if result["reason"] != NO_STRATEGY_MATCH_REASON:
        raise AssertionError
    if executed != ["first_abstain", "second_abstain", "third_abstain"]:
        raise AssertionError


def test_invalid_result_raises_and_stops_chain() -> None:
    """Invalid contract output should raise and halt further evaluation."""
    executed: list[str] = []

    with pytest.raises(StrategyContractError, match="first_invalid"):
        _ = execute_strategy_chain(
            strategies=(
                ("first_invalid", _invalid_strategy("first_invalid", executed)),
                ("second_abstain", _abstain_strategy("second_abstain", executed)),
            ),
        )

    if executed != ["first_invalid"]:
        raise AssertionError


def _abstain_strategy(label: str, executed: list[str]) -> Callable[[], object]:
    def _strategy() -> object:
        executed.append(label)
        return {"status": "ABSTAIN", "reason": f"{label}_reason"}

    return _strategy


def _invalid_strategy(label: str, executed: list[str]) -> Callable[[], object]:
    def _strategy() -> object:
        executed.append(label)
        return {"status": "INVALID", "reason": f"{label}_reason"}

    return _strategy


def _distinct_strategy(label: str, executed: list[str]) -> Callable[[], object]:
    def _strategy() -> object:
        executed.append(label)
        return {"status": "DISTINCT", "reason": f"{label}_reason"}

    return _strategy


def _duplicate_strategy(label: str, executed: list[str]) -> Callable[[], object]:
    def _strategy() -> object:
        executed.append(label)
        return {"status": "DUPLICATE", "score": 1.0, "reason": f"{label}_reason"}

    return _strategy
