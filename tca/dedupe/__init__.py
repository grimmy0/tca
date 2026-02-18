"""Deduplication module for TCA."""

from .candidate_selection import (
    MAX_CANDIDATES_DEFAULT,
    CandidateRecord,
    select_candidates,
)
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
from .strategy_chain import (
    NO_STRATEGY_MATCH_REASON,
    StrategyChain,
    execute_strategy_chain,
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
from .title_similarity_strategy import (
    TITLE_SIMILARITY_MATCH_REASON,
    TITLE_SIMILARITY_MISMATCH_REASON,
    TITLE_SIMILARITY_SHORT_TITLE_REASON,
    TITLE_SIMILARITY_THRESHOLD_DEFAULT,
    evaluate_title_similarity,
)

__all__ = [
    "CONTENT_HASH_MATCH_REASON",
    "CONTENT_HASH_MISMATCH_REASON",
    "CONTENT_HASH_MISSING_REASON",
    "EXACT_URL_MATCH_REASON",
    "EXACT_URL_MISMATCH_REASON",
    "EXACT_URL_MISSING_REASON",
    "MAX_CANDIDATES_DEFAULT",
    "NO_STRATEGY_MATCH_REASON",
    "STRATEGY_STATUSES",
    "TITLE_SIMILARITY_MATCH_REASON",
    "TITLE_SIMILARITY_MISMATCH_REASON",
    "TITLE_SIMILARITY_SHORT_TITLE_REASON",
    "TITLE_SIMILARITY_THRESHOLD_DEFAULT",
    "AbstainResult",
    "CandidateRecord",
    "DistinctResult",
    "DuplicateResult",
    "StrategyCallable",
    "StrategyChain",
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
    "evaluate_title_similarity",
    "execute_strategy_chain",
    "run_strategy",
    "select_candidates",
]
