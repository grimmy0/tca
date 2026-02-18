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


@pytest.mark.parametrize(
    "value",
    [True, False, float("nan"), float("inf"), -float("inf")],
)
def test_duplicate_score_rejects_bool_and_non_finite_values(value: object) -> None:
    """Ensure DUPLICATE score disallows bools and non-finite floats."""
    with pytest.raises(StrategyContractError):
        _ = coerce_strategy_result(
            {"status": "DUPLICATE", "score": value, "reason": "invalid_score"},
        )


def test_metadata_must_be_mapping_when_provided() -> None:
    """Ensure non-mapping metadata values are rejected."""
    with pytest.raises(StrategyContractError, match="metadata` must be a mapping"):
        _ = coerce_strategy_result(
            {
                "status": "DISTINCT",
                "reason": "invalid_metadata",
                "metadata": ["not", "a", "mapping"],
            },
        )


def test_metadata_keys_must_be_strings() -> None:
    """Ensure metadata mappings with non-string keys are rejected."""
    with pytest.raises(StrategyContractError, match="metadata` keys must be strings"):
        _ = coerce_strategy_result(
            {
                "status": "ABSTAIN",
                "reason": "invalid_metadata_keys",
                "metadata": {1: "x"},
            },
        )
