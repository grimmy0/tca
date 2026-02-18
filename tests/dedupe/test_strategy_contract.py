"""Unit tests for dedupe strategy result contract enforcement."""

from __future__ import annotations

from typing import assert_type, cast

import pytest

from tca.dedupe import (
    StrategyContractError,
    StrategyResult,
    coerce_strategy_result,
    run_strategy,
)


def test_engine_rejects_invalid_strategy_return_values() -> None:
    """Ensure non-mapping strategy results are rejected immediately."""

    def _invalid_strategy() -> object:
        return "DUPLICATE"

    with pytest.raises(StrategyContractError):
        _ = run_strategy(strategy_name="invalid", strategy=_invalid_strategy)


def test_contract_is_type_checked_and_unit_tested() -> None:
    """Ensure strategy result coercion keeps the static union contract."""
    raw = cast("object", {"status": "ABSTAIN", "reason": "too_short"})
    result = coerce_strategy_result(raw)
    _ = assert_type(result, StrategyResult)
    if result["status"] != "ABSTAIN":
        raise AssertionError


def test_unknown_statuses_fail_fast() -> None:
    """Ensure unexpected statuses fail with a contract error."""

    def _unknown_status_strategy() -> object:
        return {"status": "UNKNOWN", "reason": "bug"}

    with pytest.raises(StrategyContractError, match="Unknown strategy status"):
        _ = run_strategy(
            strategy_name="unknown_status",
            strategy=_unknown_status_strategy,
        )
