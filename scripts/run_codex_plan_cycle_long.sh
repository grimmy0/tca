#!/usr/bin/env bash
set -euo pipefail

# Long-run launcher for Codex plan automation with conservative rate-limit settings.
# All values can be overridden via environment variables.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

CODEX_BIN="${CODEX_BIN:-codex}"
CODEX_MODEL="${CODEX_MODEL:-}"

MAX_CYCLES="${MAX_CYCLES:-100000}"
SLEEP_SECONDS="${SLEEP_SECONDS:-0}"
CODEX_EXEC_COOLDOWN_SECONDS="${CODEX_EXEC_COOLDOWN_SECONDS:-600}"
RETRYABLE_RATE_LIMIT_MAX_RETRIES="${RETRYABLE_RATE_LIMIT_MAX_RETRIES:-12}"
RETRYABLE_RATE_LIMIT_BACKOFF_BASE_SECONDS="${RETRYABLE_RATE_LIMIT_BACKOFF_BASE_SECONDS:-5}"
RETRYABLE_RATE_LIMIT_BACKOFF_MAX_SECONDS="${RETRYABLE_RATE_LIMIT_BACKOFF_MAX_SECONDS:-600}"
QUOTA_WAIT_INTERVAL_SECONDS="${QUOTA_WAIT_INTERVAL_SECONDS:-3600}"
MAX_QUOTA_WAITS="${MAX_QUOTA_WAITS:-168}"
DOCS_REVIEW_INTERVAL="${DOCS_REVIEW_INTERVAL:-0}"

cmd=(
  python
  scripts/codex_plan_cycle_runner.py
  --repo-root
  "${REPO_ROOT}"
  --codex-bin
  "${CODEX_BIN}"
  --max-cycles
  "${MAX_CYCLES}"
  --sleep-seconds
  "${SLEEP_SECONDS}"
  --allow-dirty-start
  --docs-review-interval
  "${DOCS_REVIEW_INTERVAL}"
  --codex-exec-cooldown-seconds
  "${CODEX_EXEC_COOLDOWN_SECONDS}"
  --retryable-rate-limit-max-retries
  "${RETRYABLE_RATE_LIMIT_MAX_RETRIES}"
  --retryable-rate-limit-backoff-base-seconds
  "${RETRYABLE_RATE_LIMIT_BACKOFF_BASE_SECONDS}"
  --retryable-rate-limit-backoff-max-seconds
  "${RETRYABLE_RATE_LIMIT_BACKOFF_MAX_SECONDS}"
  --quota-wait-interval
  "${QUOTA_WAIT_INTERVAL_SECONDS}"
  --max-quota-waits
  "${MAX_QUOTA_WAITS}"
)

if [[ -n "${CODEX_MODEL}" ]]; then
  cmd+=(--model "${CODEX_MODEL}")
fi

echo "Launching Codex long-run automation from ${REPO_ROOT}"
echo "Cooldown between codex exec calls: ${CODEX_EXEC_COOLDOWN_SECONDS}s"
echo "Command: ${cmd[*]}"

exec "${cmd[@]}"
