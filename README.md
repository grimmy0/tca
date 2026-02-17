# TCA - Telegram Channel Aggregator

TCA is a local-first Telegram channel aggregator that runs on your machine, merges updates from channels you follow, and produces one deduplicated thread with explainable matching decisions.

## Status

- Phase: implementation in progress (storage, settings, auth bootstrap/unlock, channel/group APIs, poll-now jobs, notifications list/ack, scheduler core loop, ingest helpers, and Telegram auth start/verify are implemented; dedupe/UI/ops remain planned).
- Scope: Telegram only.
- Deployment target (planned): single local Docker container with SQLite persistence.

## Current Implementation Snapshot

- FastAPI app factory with lifespan-managed migrations, settings seeding, bootstrap bearer token generation, and CORS allowlist.
- Auth primitives: unlock modes, Argon2id KDF, envelope encryption helpers, session storage, key-rotation metadata, auth session state.
- API endpoints: `/health` (public), `/settings/{key}`, `/channels`, `/channel-groups`, `/jobs/poll-now/{channel_id}`, `/notifications`, `/notifications/{notification_id}/ack`, `/auth/telegram/start`, `/auth/telegram/verify-code`, `/auth/telegram/verify-password`, and `/openapi.json` (bearer auth required).
- Scheduler core loop with jittered poll cadence and poll job enqueueing.
- Ingest helpers: cursor state, bounded pagination, raw message upsert, flood-wait handling, account risk escalation, ingest error capture.
- Storage: SQLite WAL/PRAGMAs, `BEGIN IMMEDIATE`, writer queue, migrations for core schema + FTS/ops tables.

## Why TCA

If you follow many Telegram channels, the same story appears repeatedly with minor wording differences or tracking-link variants. TCA reduces feed noise by grouping duplicate posts and exposing one representative item with attribution.

## Product Scope

In scope:

- Telegram user-account authentication (Telethon, MTProto).
- Polling-based ingestion.
- Multi-strategy deduplication with configurable horizon.
- Local API plus minimal local web UI.
- Local storage, retention, backups, and notifications.

Out of scope:

- Managed/cloud service.
- Multi-provider aggregation (RSS/YouTube/Reddit/etc.).
- Bot API replacement for user-account access.

## Documentation Map

Use this section as the navigation hub for the repo.

- Architecture and product design: [`docs/option-a-local-design.md`](docs/option-a-local-design.md)
- Commit-atomic implementation roadmap: [`docs/implementation-plan.md`](docs/implementation-plan.md)
- Shared testing/concurrency guide: [`docs/testing-guide.md`](docs/testing-guide.md)
- Assistant-specific working context (non-product specs): [`CLAUDE.md`](CLAUDE.md)
- Assistant-specific working context (non-product specs): [`GEMINI.md`](GEMINI.md)

Suggested reading order:

1. [`docs/option-a-local-design.md`](docs/option-a-local-design.md)
2. [`docs/implementation-plan.md`](docs/implementation-plan.md)
3. [`docs/testing-guide.md`](docs/testing-guide.md)
4. [`README.md`](README.md) for day-to-day orientation

## Architecture Snapshot

TCA follows a modular local-monolith layout:

- `api/`: HTTP endpoints and auth middleware.
- `ui/`: server-rendered pages (Jinja2 + HTMX + Pico CSS) (planned).
- `auth/`: Telegram login flow and encrypted session handling (partial; OTP start/verify + storage primitives).
- `ingest/`: polling and message fetch logic (helpers only; no worker execution yet).
- `normalize/`: canonical item transformation (planned).
- `dedupe/`: strategy chain and cluster operations (planned).
- `storage/`: SQLAlchemy repositories and write serialization.
- `scheduler/`: polling cadence, jitter, pause-aware selection (implemented); backoff/worker execution (planned).
- `ops/`: retention, backup, health, graceful shutdown tasks (planned).

## Deduplication Model (Planned)

Strategy order (short-circuiting):

1. `exact_url`
2. `content_hash`
3. `title_similarity`

Core behaviors:

- Candidate reduction before expensive comparisons.
- Configurable horizon (global + per channel group override).
- Explainability via persisted decision records.
- Deterministic cluster merge and representative item selection.

## Security Model (Partially Implemented)

- All API endpoints except `/health` require bearer auth.
- Bearer token is generated once, stored as SHA-256 digest only.
- Bootstrap bearer token is written once to `TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH` (default `/data/bootstrap-bearer-token.txt`).
- Telegram session material is encrypted at rest.
- KEK is derived with Argon2id from user passphrase.
- Startup modes:
  - `secure-interactive` (default)
  - `auto-unlock` (optional, lower security)

Implemented today: bearer auth enforcement, bootstrap token output, unlock modes, KEK derivation, session encryption helpers, and auth session state storage.

## Data and Reliability Model (Planned)

- SQLite with WAL mode and mandatory PRAGMAs.
- `BEGIN IMMEDIATE` for write transactions.
- Single in-process writer queue for mutating operations.
- Ordered retention pruning with bounded batch sizes.
- Nightly backups via SQLite Online Backup API and integrity checks.

Implemented today: WAL/PRAGMA enforcement, `BEGIN IMMEDIATE`, and writer queue; retention/backup jobs are not wired yet.

## Configuration Surface (Partially Implemented)

Static environment variables (restart required):

- `TCA_DB_PATH`
- `TCA_BIND`
- `TCA_MODE`
- `TCA_LOG_LEVEL`
- `TCA_SECRET_FILE`
- `TCA_CORS_ALLOW_ORIGINS` (comma-separated allowlist)
- `TCA_BOOTSTRAP_TOKEN_OUTPUT_PATH`

Dynamic settings (runtime editable via API; UI planned):

- dedupe horizon and thresholds
- scheduler polling limits and intervals
- retention windows
- backup retention count

Implemented today: allowlisted dynamic settings read/write via `/settings/{key}`.

## Local Development

Current codebase is still scaffold-first. Use:

```bash
# install runtime + dev tooling
uv sync --extra dev

# run application placeholder
uv run python main.py

# run strict lint/type gate manually
scripts/lint_strict.sh

# install git pre-commit hook (runs strict lint gate)
uv run pre-commit install
```

Strict pre-commit gate is enabled and runs:

- Ruff (`--select ALL`) and format checks
- MyPy strict mode
- Pyright warnings-as-errors
- BasedPyright all-mode checks
- plan-to-test traceability validation (`scripts/validate_plan_criteria.py`)
- API response-model discipline checks (`scripts/check_api_response_models.py`)
- TestClient context-manager convention checks (`scripts/check_testclient_context.py`)

## Execution Model for Contributors

Development is intentionally commit-atomic.

- Plan source of truth: `docs/implementation-plan.md`
- Each item is one commit-sized change.
- Each item has binary acceptance criteria.
- Each completed acceptance criterion must include explicit `[Tests: tests/...::test_...]` mappings.
- Each item includes verification commands.

If you are implementing features, pick the next unchecked plan item and do not batch unrelated work into the same commit.

## CI Guardrails

GitHub Actions enforces three blocking jobs:

- `lint-strict`: strict lint/type/policy checks from `scripts/lint_strict.sh`
- `test-suite`: full `pytest` run
- `contract-gates`: plan criteria mapping validation, API/TestClient policy checks, and targeted contract suites (`tests/contracts`, `tests/app`, `tests/api`, `tests/logging`)

PR reviews use `.github/pull_request_template.md`, which includes lifecycle, API-schema, logging, and traceability checklists.

## Notes on Repo Documents

`CLAUDE.md` and `GEMINI.md` provide assistant/tooling context for automated coding workflows. Product and engineering source-of-truth remains:

1. [`docs/option-a-local-design.md`](docs/option-a-local-design.md)
2. [`docs/implementation-plan.md`](docs/implementation-plan.md)

## License

No license file has been added yet.
