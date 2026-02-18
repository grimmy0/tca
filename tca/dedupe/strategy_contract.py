"""Strategy result contract primitives for the dedupe engine."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Literal, NotRequired, TypedDict, assert_never, cast

STRATEGY_STATUSES: tuple[str, str, str] = ("DUPLICATE", "DISTINCT", "ABSTAIN")
StrategyStatus = Literal["DUPLICATE", "DISTINCT", "ABSTAIN"]
StrategyMetadata = dict[str, object]


class DuplicateResult(TypedDict):
    """Contract payload for duplicate strategy outcomes."""

    status: Literal["DUPLICATE"]
    score: float
    reason: str
    metadata: NotRequired[StrategyMetadata]


class DistinctResult(TypedDict):
    """Contract payload for non-duplicate strategy outcomes."""

    status: Literal["DISTINCT"]
    reason: str
    metadata: NotRequired[StrategyMetadata]


class AbstainResult(TypedDict):
    """Contract payload for abstain strategy outcomes."""

    status: Literal["ABSTAIN"]
    reason: str
    metadata: NotRequired[StrategyMetadata]


StrategyResult = DuplicateResult | DistinctResult | AbstainResult
StrategyCallable = Callable[[], object]


class StrategyContractError(ValueError):
    """Raised when a strategy violates the required result contract."""


def duplicate(
    *,
    score: float,
    reason: str,
    metadata: Mapping[str, object] | None = None,
) -> DuplicateResult:
    """Return a typed DUPLICATE strategy result."""
    result: DuplicateResult = {"status": "DUPLICATE", "score": score, "reason": reason}
    if metadata is not None:
        result["metadata"] = dict(metadata)
    return result


def distinct(
    *,
    reason: str,
    metadata: Mapping[str, object] | None = None,
) -> DistinctResult:
    """Return a typed DISTINCT strategy result."""
    result: DistinctResult = {"status": "DISTINCT", "reason": reason}
    if metadata is not None:
        result["metadata"] = dict(metadata)
    return result


def abstain(
    *,
    reason: str,
    metadata: Mapping[str, object] | None = None,
) -> AbstainResult:
    """Return a typed ABSTAIN strategy result."""
    result: AbstainResult = {"status": "ABSTAIN", "reason": reason}
    if metadata is not None:
        result["metadata"] = dict(metadata)
    return result


def run_strategy(*, strategy_name: str, strategy: StrategyCallable) -> StrategyResult:
    """Execute one strategy and enforce contract validity."""
    raw_result = strategy()
    try:
        return coerce_strategy_result(raw_result)
    except StrategyContractError as exc:
        message = f"Strategy {strategy_name!r} returned invalid result contract: {exc}"
        raise StrategyContractError(message) from exc


def coerce_strategy_result(result: object) -> StrategyResult:
    """Validate and normalize unknown strategy output to a contract result."""
    if not isinstance(result, Mapping):
        message = "Strategy result must be a mapping with `status` and `reason` fields."
        raise StrategyContractError(message)

    result_map = cast("Mapping[str, object]", result)

    status_obj = result_map.get("status")
    if not isinstance(status_obj, str):
        message = "Strategy result `status` must be a string."
        raise StrategyContractError(message)
    if status_obj not in STRATEGY_STATUSES:
        message = f"Unknown strategy status: {status_obj!r}"
        raise StrategyContractError(message)
    status = cast("StrategyStatus", status_obj)

    reason_obj = result_map.get("reason")
    if not isinstance(reason_obj, str):
        message = "Strategy result `reason` must be a string."
        raise StrategyContractError(message)
    reason = reason_obj.strip()
    if not reason:
        message = "Strategy result `reason` must be non-empty."
        raise StrategyContractError(message)
    metadata = _coerce_metadata(result=result_map)

    if status == "DUPLICATE":
        score = _coerce_score(result=result_map)
        return duplicate(score=score, reason=reason, metadata=metadata)
    if status == "DISTINCT":
        return distinct(reason=reason, metadata=metadata)
    if status == "ABSTAIN":
        return abstain(reason=reason, metadata=metadata)

    assert_never(status)


def _coerce_score(*, result: Mapping[str, object]) -> float:
    score_obj = result.get("score")
    if isinstance(score_obj, bool) or not isinstance(score_obj, int | float):
        message = "DUPLICATE strategy result `score` must be numeric."
        raise StrategyContractError(message)
    score = float(score_obj)
    if not math.isfinite(score):
        message = "DUPLICATE strategy result `score` must be finite."
        raise StrategyContractError(message)
    return score


def _coerce_metadata(*, result: Mapping[str, object]) -> StrategyMetadata | None:
    metadata_obj = result.get("metadata")
    if metadata_obj is None:
        return None
    if not isinstance(metadata_obj, Mapping):
        message = "Strategy result `metadata` must be a mapping when provided."
        raise StrategyContractError(message)

    metadata_map = cast("Mapping[object, object]", metadata_obj)
    metadata: StrategyMetadata = {}
    for key, value in metadata_map.items():
        if not isinstance(key, str):
            message = "Strategy result `metadata` keys must be strings."
            raise StrategyContractError(message)
        metadata[key] = value
    return metadata
