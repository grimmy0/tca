# TCA Implementation Plan (Commit-Atomic)

## Document Scope

This plan implements `docs/option-a-local-design.md` (Version 3, last updated 2026-02-16).

Rules used in this plan:

- Every item below is one commit-sized atomic change.
- Every item has binary acceptance criteria (pass/fail).
- Acceptance criteria are written so they can be validated by command output, API response, or DB state.
- Order is intentional; do not reorder unless dependencies are explicitly adjusted.
- This file is a live tracker during execution:
  - mark completed acceptance criteria as `[x]`,
  - add `Execution record` with date, commit hash, and verification summary.

## Global Quality Gate (applies to every item)

An item is commit-ready only if all are true:

- Targeted tests for the changed area pass.
- No unrelated files are modified.
- Lint/type checks for touched modules pass.
- If API/DB behavior changes, corresponding docs or migration files are included in the same commit.

---

## Phase 0: Foundation and Project Shape

### C001 - Create Runtime Package Skeleton

- Change:
  - Create top-level package layout matching design module boundaries: `tca/api`, `tca/ui`, `tca/auth`, `tca/ingest`, `tca/normalize`, `tca/dedupe`, `tca/storage`, `tca/scheduler`, `tca/ops`.
  - Add minimal `__init__.py` files.
- Acceptance criteria:
  - [x] All listed directories exist and are importable Python packages.
  - [x] `uv run python -c "import tca"` exits `0`.
  - [x] No runtime behavior beyond importability is introduced.
- Verification:
  - `find tca -maxdepth 2 -type f | sort`
  - `uv run python -c "import tca"`
- Execution record:
  - Date: 2026-02-16
  - Commit: `2b144a7`
  - Verification summary:
    - `find tca -maxdepth 2 -type f | sort` returned all required package files.
    - `uv run python -c "import tca"` completed successfully (exit `0`).

### C002 - Add Baseline Dependencies and Extras

- Change:
  - Update `pyproject.toml` dependencies for Phase 1 stack: FastAPI, Uvicorn, SQLAlchemy, aiosqlite, Alembic, Telethon (1.42.x), Jinja2, RapidFuzz, argon2-cffi, cryptography.
  - Add `dev` extras (pytest, httpx, ruff, mypy).
- Acceptance criteria:
  - [x] `pyproject.toml` contains all required runtime dependencies.
  - [x] Telethon pin is constrained to `1.42.*`.
  - [x] `uv lock` completes successfully.
- Verification:
  - `cat pyproject.toml`
  - `uv lock`
- Execution record:
  - Date: 2026-02-16
  - Commit: `6e43a20`
  - Verification summary:
    - `pyproject.toml` includes planned runtime dependencies and `dev` extra group.
    - Telethon constraint is `>=1.42,<1.43` (1.42.x line).
    - `uv lock` succeeded (`Resolved 51 packages in 1.10s`).

### C003 - Add Tooling Configuration

- Change:
  - Add lint/type/test configuration (`ruff`, `pytest`, `mypy`) in `pyproject.toml`.
  - Add minimal `tests/` package bootstrap.
- Acceptance criteria:
  - [ ] `uv run ruff check .` exits `0` on initial scaffold.
  - [ ] `uv run pytest -q` exits `0` with zero or baseline tests.
  - [ ] `uv run mypy tca` exits `0` for current code.
- Verification:
  - `uv run ruff check .`
  - `uv run pytest -q`
  - `uv run mypy tca`

### C003A - Add Shared Testing Harness and SQLite Concurrency Guide

- Change:
  - Add `tests/conftest.py` with reusable fixtures for async DB session setup and deterministic concurrency tests.
  - Add `docs/testing-guide.md` with a focused section showing how to reproduce and assert `SQLITE_BUSY` scenarios in tests.
- Acceptance criteria:
  - [ ] `tests/conftest.py` exposes fixtures used by storage tests and does not perform network calls.
  - [ ] Guide includes one runnable example for concurrent write contention and expected assertion pattern.
  - [ ] C010 tests use shared fixtures/harness instead of ad-hoc concurrency setup.
- Verification:
  - `uv run pytest -q tests/storage/test_begin_immediate.py`
  - `rg -n "SQLITE_BUSY|concurrency" docs/testing-guide.md tests/conftest.py`

### C003B - Add `MockTelegramClient` Test Double

- Change:
  - Add `tests/mocks/mock_telegram_client.py` implementing the minimal Telethon-like surface needed by Phase 1 tests.
  - Add fixture wiring to inject `MockTelegramClient` into API/auth/scheduler tests.
- Acceptance criteria:
  - [ ] Tests for Telegram flows can run without any real Telethon network interaction.
  - [ ] Mock supports deterministic success/failure scripting for OTP, flood-wait, and message fetch paths.
  - [ ] At least one auth test and one ingest test are switched to the shared mock.
- Verification:
  - `uv run pytest -q tests/api/test_telegram_auth_start.py`
  - `uv run pytest -q tests/ingest/test_flood_wait.py`

### C004 - Introduce Centralized App Settings Model

- Change:
  - Implement typed settings loader for env vars: `TCA_DB_PATH`, `TCA_BIND`, `TCA_MODE`, `TCA_LOG_LEVEL`, `TCA_SECRET_FILE`.
  - Define defaults from design.
- Acceptance criteria:
  - [ ] Settings object loads with defaults when env vars are absent.
  - [ ] Invalid values (for mode/log level) raise deterministic validation error.
  - [ ] Unit tests cover default and invalid env cases.
- Verification:
  - `uv run pytest -q tests/settings`

### C005 - Add Structured Logging Bootstrap

- Change:
  - Add logging initializer used by app startup.
  - Include request correlation ID field placeholder.
- Acceptance criteria:
  - [ ] App startup emits structured logs at configured level.
  - [ ] Log level changes with `TCA_LOG_LEVEL`.
  - [ ] Unit test asserts logger configuration behavior.
- Verification:
  - `uv run pytest -q tests/logging`

### C006 - Add App Factory and Lifespan Hooks

- Change:
  - Implement FastAPI app factory with lifespan context manager.
  - Register startup/shutdown hook stubs for DB, Telethon manager, scheduler.
- Acceptance criteria:
  - [ ] App can start and stop cleanly in tests without real Telegram calls.
  - [ ] Lifespan hooks run once per test app lifecycle.
  - [ ] Missing startup dependency fails fast with clear error.
- Verification:
  - `uv run pytest -q tests/app/test_lifespan.py`

### C007 - Add `/health` Endpoint

- Change:
  - Implement unauthenticated `GET /health` route.
  - Return deterministic JSON payload with status and timestamp.
- Acceptance criteria:
  - [ ] `GET /health` returns `200` without auth header.
  - [ ] Response schema is stable and documented.
  - [ ] Health route remains accessible when bearer auth middleware is later enabled.
- Verification:
  - `uv run pytest -q tests/api/test_health.py`

---

## Phase 1: Database Engine, Migrations, and Schema

### C008 - Add SQLAlchemy Engine/Session Wiring

- Change:
  - Implement async engine/session factory using SQLite path from settings.
  - Provide separate session helpers for read and write paths.
- Acceptance criteria:
  - [ ] Engine initializes against configured DB path.
  - [ ] Session factory can execute a simple `SELECT 1`.
  - [ ] Test fixture can create and teardown session cleanly.
- Verification:
  - `uv run pytest -q tests/storage/test_engine.py`

### C009 - Apply Mandatory SQLite PRAGMA Settings

- Change:
  - Add engine-connect event handlers enforcing WAL, synchronous, FK, busy_timeout pragmas.
- Acceptance criteria:
  - [ ] Runtime PRAGMA values match design on every fresh connection.
  - [ ] Test verifies each PRAGMA value exactly.
  - [ ] Regression test fails if any PRAGMA is removed.
- Verification:
  - `uv run pytest -q tests/storage/test_sqlite_pragmas.py`

### C010 - Enforce `BEGIN IMMEDIATE` for Write Transactions

- Change:
  - Add SQLAlchemy begin event hook for writer connections to issue `BEGIN IMMEDIATE`.
- Acceptance criteria:
  - [ ] Writer transaction begins in `IMMEDIATE` mode.
  - [ ] Read transactions remain non-writing and unaffected.
  - [ ] Test demonstrates writer lock acquisition behavior deterministically.
- Verification:
  - `uv run pytest -q tests/storage/test_begin_immediate.py`

### C011 - Initialize Alembic with SQLite Batch Mode

- Change:
  - Add Alembic environment and configure `render_as_batch=True`.
  - Link migration configuration notes to `docs/migration-policy.md`.
- Acceptance criteria:
  - [ ] `alembic upgrade head` works on empty DB.
  - [ ] Alembic config clearly enables batch mode.
  - [ ] Migration command is invokable from project root.
- Verification:
  - `uv run alembic upgrade head`
  - `uv run alembic current`

### C011A - Add Migration Policy Note for SQLite Batch Mode

- Change:
  - Add `docs/migration-policy.md` explaining why `render_as_batch=True` is mandatory for SQLite and when to use batch migrations.
  - Include a short migration checklist (pre-checks, lock considerations, rollback expectations).
- Acceptance criteria:
  - [ ] Policy document explicitly references SQLite `ALTER TABLE` limitations and project batch-mode requirement.
  - [ ] Checklist is concise and directly usable in migration PRs.
  - [ ] C011 references this document in implementation notes.
- Verification:
  - `rg -n "render_as_batch|SQLite|ALTER TABLE" docs/migration-policy.md docs/implementation-plan.md`

### C012 - Create Base Migration: Accounts, Channels, Groups

- Change:
  - Add tables: `telegram_accounts`, `telegram_channels`, `channel_groups`, `channel_group_members`, `channel_state`.
  - Include group membership uniqueness (single group per channel).
- Acceptance criteria:
  - [ ] All tables exist after migration.
  - [ ] `channel_group_members.channel_id` unique constraint exists.
  - [ ] FK relationships resolve correctly.
- Verification:
  - `uv run pytest -q tests/migrations/test_base_schema_groups.py`

### C013 - Create Base Migration: Content and Dedupe Tables

- Change:
  - Add tables: `raw_messages`, `items`, `dedupe_clusters`, `dedupe_members`, `dedupe_decisions`.
  - Include `items.raw_message_id` nullable FK with `ON DELETE SET NULL`.
- Acceptance criteria:
  - [ ] `items.raw_message_id` FK delete action is `SET NULL`.
  - [ ] `items(channel_id, message_id)` uniqueness exists.
  - [ ] `dedupe_members` uniqueness exists for `(cluster_id, item_id)`.
- Verification:
  - `uv run pytest -q tests/migrations/test_content_dedupe_schema.py`

### C014 - Create Base Migration: Ops Tables

- Change:
  - Add tables: `ingest_errors`, `notifications`, `settings`.
  - Add `settings.key` unique constraint.
- Acceptance criteria:
  - [ ] All ops/config tables exist.
  - [ ] `settings.key` is unique.
  - [ ] `ingest_errors` has required stage and timestamp fields.
- Verification:
  - `uv run pytest -q tests/migrations/test_ops_schema.py`

### C015 - Add Required Secondary Indexes

- Change:
  - Add all Phase 1 indexes from design (published_at/hash/indexes/group indexes/error indexes).
- Acceptance criteria:
  - [ ] Each index defined in design is present in DB metadata.
  - [ ] Missing index causes test failure.
  - [ ] Query explain snapshots include index usage for representative read paths.
- Verification:
  - `uv run pytest -q tests/migrations/test_indexes.py`

### C016 - Add FTS5 External-Content Table Migration

- Change:
  - Create FTS5 virtual table linked to `items` as external content.
- Acceptance criteria:
  - [ ] FTS5 table exists and is queryable with `MATCH`.
  - [ ] Migration is reversible.
  - [ ] FTS migration runs on SQLite without manual intervention.
- Verification:
  - `uv run pytest -q tests/migrations/test_fts_table.py`

### C017 - Add FTS5 Trigger Migration

- Change:
  - Add insert/update/delete triggers to keep FTS index synchronized with `items`.
- Acceptance criteria:
  - [ ] Insert into `items` appears in FTS results.
  - [ ] Update modifies FTS searchable text.
  - [ ] Delete removes FTS hit.
- Verification:
  - `uv run pytest -q tests/migrations/test_fts_triggers.py`

### C018 - Add Migration Runner in App Startup Path

- Change:
  - Ensure startup executes `alembic upgrade head` before serving API.
- Acceptance criteria:
  - [ ] On empty DB, app starts and schema is current.
  - [ ] On current DB, startup is idempotent.
  - [ ] API does not accept requests before migration success.
- Verification:
  - `uv run pytest -q tests/app/test_startup_migrations.py`

---

## Phase 2: Repository Layer and Config Surface

### C019 - Implement Settings Repository

- Change:
  - CRUD helpers for `settings` table with typed conversions.
- Acceptance criteria:
  - [ ] Can create/read/update by `key`.
  - [ ] Duplicate key insertion fails deterministically.
  - [ ] JSON values preserve type fidelity.
- Verification:
  - `uv run pytest -q tests/storage/test_settings_repo.py`

### C020 - Seed Default Dynamic Settings on First Boot

- Change:
  - Add bootstrap routine writing default settings only when keys absent.
- Acceptance criteria:
  - [ ] First boot inserts all design default keys.
  - [ ] Second boot does not overwrite modified values.
  - [ ] Missing single key is backfilled without touching others.
- Verification:
  - `uv run pytest -q tests/settings/test_seed_defaults.py`

### C021 - Implement Channel Group Repositories

- Change:
  - Add data access layer for group CRUD and membership operations.
- Acceptance criteria:
  - [ ] Group create/update/delete works.
  - [ ] Channel cannot be assigned to multiple groups.
  - [ ] Removing group cleans memberships.
- Verification:
  - `uv run pytest -q tests/storage/test_channel_groups_repo.py`

### C022 - Implement Channels Repository with Soft-Delete Fields

- Change:
  - Add repository for channel create/update/enable/disable operations.
- Acceptance criteria:
  - [ ] Channel can be disabled without row deletion.
  - [ ] Disabled channels are excluded from active query helper.
  - [ ] Re-enable restores active status.
- Verification:
  - `uv run pytest -q tests/storage/test_channels_repo.py`

### C022A - Implement Single Writer Queue Service

- Change:
  - Add `tca/storage/writer_queue.py` to serialize write operations through one in-process async queue.
  - Add API and scheduler integration points to route all mutating DB operations through the queue abstraction.
- Acceptance criteria:
  - [ ] Only one queued write job is executed at a time (verified by concurrency test).
  - [ ] Concurrent write submissions are processed FIFO and all produce deterministic completion/error results.
  - [ ] At least one API write path and one ingest write path are switched to writer-queue execution.
- Verification:
  - `uv run pytest -q tests/storage/test_writer_queue.py`
  - `uv run pytest -q tests/api/test_settings_api.py`
  - `uv run pytest -q tests/ingest/test_raw_upsert.py`

### C023 - Add Config Resolution Service

- Change:
  - Implement runtime config resolution contract:
    - static env values,
    - dynamic settings rows,
    - group-specific horizon override.
- Acceptance criteria:
  - [ ] Global horizon comes from settings key.
  - [ ] Group override wins over global when present.
  - [ ] Missing setting falls back to seeded default value.
- Verification:
  - `uv run pytest -q tests/settings/test_resolution.py`

### C024 - Add Settings API (Read + Update Allowed Keys)

- Change:
  - Add API endpoints for dynamic settings read/update with allowlist.
- Acceptance criteria:
  - [ ] Unknown keys are rejected with `400`.
  - [ ] Allowed keys update immediately and persist.
  - [ ] Response returns effective value after write.
- Verification:
  - `uv run pytest -q tests/api/test_settings_api.py`

### C025 - Add Channel Group API Endpoints

- Change:
  - Implement all endpoints from design section 12.4.
- Acceptance criteria:
  - [ ] CRUD operations return expected status codes.
  - [ ] Membership add/remove endpoint updates join table.
  - [ ] Group horizon override can be set and cleared.
- Verification:
  - `uv run pytest -q tests/api/test_channel_groups_api.py`

### C026 - Add OpenAPI Contract Snapshot for Config/Groups

- Change:
  - Freeze and version endpoint schema snapshot for settings and group endpoints.
- Acceptance criteria:
  - [ ] Schema snapshot includes all new endpoints and payload fields.
  - [ ] CI test fails on unreviewed API contract drift.
  - [ ] Snapshot update process documented.
- Verification:
  - `uv run pytest -q tests/api/test_openapi_snapshot.py`

---

## Phase 3: API Security and Secret Handling

### C027 - Implement Bootstrap Bearer Token Generation

- Change:
  - Generate `secrets.token_urlsafe(32)` token on first run.
  - Persist only SHA-256 digest.
- Acceptance criteria:
  - [ ] Plain token is never written to DB.
  - [ ] Token is shown once at bootstrap output path.
  - [ ] Re-start does not rotate token automatically.
- Verification:
  - `uv run pytest -q tests/auth/test_bootstrap_token.py`

### C028 - Implement Bearer Auth Middleware

- Change:
  - Enforce bearer auth on all non-health routes.
  - Compare token digest with constant-time compare.
- Acceptance criteria:
  - [ ] Unauthenticated protected route returns `401`.
  - [ ] Invalid token returns `401`.
  - [ ] Valid token returns `200` for protected route.
- Verification:
  - `uv run pytest -q tests/api/test_bearer_auth.py`

### C029 - Add CORS Allowlist Enforcement

- Change:
  - Implement default-deny CORS with explicit allowlist config.
- Acceptance criteria:
  - [ ] No CORS headers when origin not allowlisted.
  - [ ] Allowlisted origin receives expected CORS headers.
  - [ ] Behavior is covered by API tests.
- Verification:
  - `uv run pytest -q tests/api/test_cors.py`

### C030 - Implement Envelope Encryption Utilities

- Change:
  - Implement DEK generation + data encryption/decryption helpers.
  - Implement KEK wrapping/unwrapping flow.
- Acceptance criteria:
  - [ ] Encrypt/decrypt round trip returns exact original bytes.
  - [ ] Decrypt with wrong key fails deterministically.
  - [ ] Ciphertext payload includes version metadata.
- Verification:
  - `uv run pytest -q tests/auth/test_encryption_utils.py`

### C031 - Implement Argon2id KEK Derivation

- Change:
  - Implement passphrase KDF using Argon2id with design parameters.
- Acceptance criteria:
  - [ ] KDF parameters match design values exactly.
  - [ ] Same passphrase+salt yields deterministic key.
  - [ ] Different salt yields different key.
- Verification:
  - `uv run pytest -q tests/auth/test_kdf.py`

### C032 - Implement Startup Unlock Modes

- Change:
  - Add `secure-interactive` and `auto-unlock` startup behavior.
- Acceptance criteria:
  - [ ] Secure mode requires unlock action before sensitive operations.
  - [ ] Auto-unlock mode reads mounted secret file.
  - [ ] Missing secret in auto mode fails startup with actionable error.
- Verification:
  - `uv run pytest -q tests/auth/test_unlock_modes.py`

### C033 - Persist Encrypted Telegram Session Material

- Change:
  - Add persistence logic for encrypted session blob in `telegram_accounts`.
- Acceptance criteria:
  - [ ] Stored session data is encrypted (not plaintext StringSession).
  - [ ] Session round-trip through DB decrypts correctly.
  - [ ] Incorrect KEK prevents session load.
- Verification:
  - `uv run pytest -q tests/auth/test_session_storage.py`

### C034 - Implement Crash-Safe Key Rotation Metadata

- Change:
  - Add rotation state tracking table/fields and row-version markers.
- Acceptance criteria:
  - [ ] Rotation state persists progress.
  - [ ] Interrupted rotation can resume at next pending row.
  - [ ] Completion state only set when all targeted rows rotated.
- Verification:
  - `uv run pytest -q tests/auth/test_key_rotation_resume.py`

---

## Phase 4: Telegram Auth Flow and Account Lifecycle

### C035 - Add Telethon Client Manager

- Change:
  - Implement shared Telethon client manager in app state.
  - Lifecycle hooks connect/disconnect clients.
- Acceptance criteria:
  - [ ] Client manager initializes during app startup.
  - [ ] Clients disconnect on app shutdown.
  - [ ] No per-request client creation occurs.
- Verification:
  - `uv run pytest -q tests/telegram/test_client_manager.py`

### C036 - Add Auth Session State Storage for Login Wizard

- Change:
  - Add temporary auth session state model for phone/code/password steps.
- Acceptance criteria:
  - [ ] Auth session state has expiry.
  - [ ] Expired session is rejected.
  - [ ] Parallel auth sessions for different users are isolated.
- Verification:
  - `uv run pytest -q tests/auth/test_auth_session_state.py`

### C037 - Implement `POST /auth/telegram/start`

- Change:
  - Accept `api_id`, `api_hash`, phone number; request OTP via Telethon.
- Acceptance criteria:
  - [ ] Valid payload returns auth session token/id.
  - [ ] Invalid API credentials return controlled error.
  - [ ] No OTP or credential secrets are logged.
- Verification:
  - `uv run pytest -q tests/api/test_telegram_auth_start.py`

### C038 - Implement `POST /auth/telegram/verify-code`

- Change:
  - Verify OTP and transition auth session state.
- Acceptance criteria:
  - [ ] Correct code advances to authenticated or password-required state.
  - [ ] Wrong code returns deterministic error response.
  - [ ] Replayed/expired code path returns failure.
- Verification:
  - `uv run pytest -q tests/api/test_telegram_auth_verify_code.py`

### C039 - Implement `POST /auth/telegram/verify-password`

- Change:
  - Complete 2FA password step when required.
- Acceptance criteria:
  - [ ] Correct password finalizes login.
  - [ ] Wrong password returns retryable error.
  - [ ] Endpoint rejects calls when password step not required.
- Verification:
  - `uv run pytest -q tests/api/test_telegram_auth_verify_password.py`

### C040 - Persist StringSession After Successful Login

- Change:
  - Convert authenticated Telethon session to StringSession and persist encrypted.
- Acceptance criteria:
  - [ ] Post-login account row created/updated in `telegram_accounts`.
  - [ ] Session value is encrypted at rest.
  - [ ] Later client initialization can reuse saved session without OTP.
- Verification:
  - `uv run pytest -q tests/telegram/test_stringsession_persistence.py`

### C041 - Implement Registration/Login Failure Notifications

- Change:
  - On registration block/auth failure, write notification with actionable message.
- Acceptance criteria:
  - [ ] Expected failure classes produce `auth_registration_blocked` or related notification.
  - [ ] Notification severity is set correctly.
  - [ ] Notification payload includes retry guidance.
- Verification:
  - `uv run pytest -q tests/notifications/test_auth_notifications.py`

### C042 - Add Account Pause/Resume Flags

- Change:
  - Add account-level pause field and resume operation used by risk controls.
- Acceptance criteria:
  - [ ] Paused account channels are excluded from scheduler selection.
  - [ ] Resume operation clears pause state.
  - [ ] Pause reason is persisted.
- Verification:
  - `uv run pytest -q tests/ingest/test_account_pause_flags.py`

---

## Phase 5: Channel and Source Management APIs

### C043 - Implement Channels CRUD API

- Change:
  - Add API for create/list/update channels (Telegram-only schema).
- Acceptance criteria:
  - [ ] Channel create validates required Telegram identifiers.
  - [ ] List endpoint returns only caller-visible channels.
  - [ ] Update endpoint persists polling-related fields.
- Verification:
  - `uv run pytest -q tests/api/test_channels_crud.py`

### C044 - Implement Channel Soft-Delete API Behavior

- Change:
  - Implement default delete mode as disable/hide only.
- Acceptance criteria:
  - [ ] `DELETE /channels/{id}` marks channel disabled.
  - [ ] Historical items remain queryable.
  - [ ] Disabled channel no longer scheduled for polling.
- Verification:
  - `uv run pytest -q tests/api/test_channel_soft_delete.py`

### C045 - Implement Channel `purge=true` Delete Path

- Change:
  - Add hard delete flow with cascade recomputation hooks.
- Acceptance criteria:
  - [ ] Raw/items rows for channel are removed.
  - [ ] Affected clusters are recomputed and empty clusters removed.
  - [ ] Audit record is stored.
- Verification:
  - `uv run pytest -q tests/api/test_channel_purge_delete.py`

### C046 - Add Manual Poll Trigger Endpoint

- Change:
  - Implement `POST /jobs/poll-now/{channel_id}`.
- Acceptance criteria:
  - [ ] Trigger enqueues poll job for active channel.
  - [ ] Disabled/paused channel returns deterministic rejection.
  - [ ] Endpoint response includes job correlation ID.
- Verification:
  - `uv run pytest -q tests/api/test_poll_now.py`

### C047 - Add API Endpoint to Read Notifications

- Change:
  - Implement notifications read/list endpoint for UI alerts.
- Acceptance criteria:
  - [ ] Endpoint returns notifications sorted by recency.
  - [ ] Supports filtering by severity/type.
  - [ ] Protected by bearer auth.
- Verification:
  - `uv run pytest -q tests/api/test_notifications_api.py`

### C048 - Add API Endpoint to Acknowledge Notifications

- Change:
  - Implement acknowledge action to mark notification resolved/read.
- Acceptance criteria:
  - [ ] Notification acknowledge updates state atomically.
  - [ ] Re-acknowledging is idempotent.
  - [ ] Response includes updated notification state.
- Verification:
  - `uv run pytest -q tests/api/test_notifications_ack.py`

---

## Phase 6: Scheduler, Polling, and Ingestion

### C049 - Implement Scheduler Core Loop

- Change:
  - Add scheduler service selecting eligible channels by interval.
- Acceptance criteria:
  - [ ] Eligible channels are selected by `next_run_at` logic.
  - [ ] Disabled channels are excluded.
  - [ ] Scheduler loop can start/stop cleanly.
- Verification:
  - `uv run pytest -q tests/scheduler/test_core_loop.py`

### C050 - Add Polling Interval + Jitter Computation

- Change:
  - Implement default interval and +/-20% jitter policy.
- Acceptance criteria:
  - [ ] Computed next run time lies within jitter bounds.
  - [ ] Jitter is deterministic under seeded RNG in tests.
  - [ ] Configurable poll interval from settings is honored.
- Verification:
  - `uv run pytest -q tests/scheduler/test_jitter.py`

### C051 - Implement Channel Cursor Persistence

- Change:
  - Persist and read cursor JSON (`last_message_id`, `next_offset_id`, `last_polled_at`).
- Acceptance criteria:
  - [ ] Cursor updates after successful poll.
  - [ ] Cursor read on next run resumes from previous state.
  - [ ] Cursor schema validation rejects malformed payload.
- Verification:
  - `uv run pytest -q tests/ingest/test_cursor_state.py`

### C052 - Implement Bounded Pagination Logic

- Change:
  - Enforce `max_pages_per_poll` and `max_messages_per_poll`.
- Acceptance criteria:
  - [ ] Poll stops when either limit is reached.
  - [ ] Unfinished pagination stores `next_offset_id`.
  - [ ] Next run continues from stored offset.
- Verification:
  - `uv run pytest -q tests/ingest/test_pagination_bounds.py`

### C053 - Implement Flood-Wait Handling

- Change:
  - Parse FloodWait, pause channel until resume timestamp, store event.
- Acceptance criteria:
  - [ ] Flood wait exception marks channel paused with exact resume time.
  - [ ] Paused channels are skipped by scheduler.
  - [ ] Notification emitted for significant pause durations.
- Verification:
  - `uv run pytest -q tests/ingest/test_flood_wait.py`

### C054 - Implement Account Risk Escalation

- Change:
  - Detect repeated flood/auth failures and pause entire account.
- Acceptance criteria:
  - [ ] Repeated threshold breaches trigger account pause.
  - [ ] High-severity notification is emitted once per pause event.
  - [ ] Polling does not continue until explicit resume.
- Verification:
  - `uv run pytest -q tests/ingest/test_account_risk_escalation.py`

### C055 - Implement Ingest Error Capture

- Change:
  - Persist errors in `ingest_errors` with stage/code/message/payload_ref.
- Acceptance criteria:
  - [ ] All error stages map to allowed enum values.
  - [ ] Error rows include non-null timestamp.
  - [ ] Ingest pipeline continues after recoverable errors.
- Verification:
  - `uv run pytest -q tests/ingest/test_error_capture.py`

### C056 - Implement Raw Message Upsert Logic

- Change:
  - Upsert `raw_messages` by `(channel_id, message_id)` with latest payload.
- Acceptance criteria:
  - [ ] Duplicate ingest of same message updates existing row, not inserts duplicate.
  - [ ] Raw payload is replaced with latest version.
  - [ ] Unique constraint violations do not crash poll loop.
- Verification:
  - `uv run pytest -q tests/ingest/test_raw_upsert.py`

---

## Phase 7: Normalization, Dedupe, and Clustering

### C057 - Implement Normalized Item Upsert

- Change:
  - Upsert `items` keyed by `(channel_id, message_id)`.
  - Maintain `raw_message_id` linkage when available.
- Acceptance criteria:
  - [ ] First ingest inserts item; re-ingest updates same row.
  - [ ] `raw_message_id` set on insert and maintained on update.
  - [ ] Deleting linked raw row sets `items.raw_message_id` to `NULL`.
- Verification:
  - `uv run pytest -q tests/normalize/test_items_upsert.py`

### C058 - Implement URL Canonicalization Utility

- Change:
  - Normalize URLs and strip tracking query params.
- Acceptance criteria:
  - [ ] Known tracking params are removed.
  - [ ] Semantically equivalent URLs normalize identically.
  - [ ] Non-URL text input is handled safely.
- Verification:
  - `uv run pytest -q tests/normalize/test_url_canonicalization.py`

### C059 - Implement Hash Normalization Pipeline

- Change:
  - Implement hash pipeline exactly as specified for `content_hash` generation.
- Acceptance criteria:
  - [ ] Same semantic input yields same normalized hash input.
  - [ ] Non-alphanumeric collapse behavior matches spec.
  - [ ] Snapshot tests lock normalization outputs.
- Verification:
  - `uv run pytest -q tests/normalize/test_hash_normalization.py`

### C060 - Implement Similarity Normalization Pipeline

- Change:
  - Implement similarity pipeline preserving token boundaries.
- Acceptance criteria:
  - [ ] Whitespace boundaries are preserved for tokenization.
  - [ ] Tracking params/wrappers are removed.
  - [ ] Snapshot tests prove divergence from hash pipeline where expected.
- Verification:
  - `uv run pytest -q tests/normalize/test_similarity_normalization.py`

### C061 - Implement Strategy Result Contract

- Change:
  - Define internal strategy return contract: `DUPLICATE`, `DISTINCT`, `ABSTAIN`.
- Acceptance criteria:
  - [ ] Engine rejects invalid strategy return values.
  - [ ] Contract is type-checked and unit-tested.
  - [ ] Unknown statuses fail fast.
- Verification:
  - `uv run pytest -q tests/dedupe/test_strategy_contract.py`

### C062 - Implement `exact_url` Strategy

- Change:
  - Add URL equality strategy using canonical URL hash/value.
- Acceptance criteria:
  - [ ] Equivalent URLs return `DUPLICATE`.
  - [ ] Non-equivalent URLs return `ABSTAIN` or `DISTINCT` per design.
  - [ ] Strategy logs reason code in decision record.
- Verification:
  - `uv run pytest -q tests/dedupe/test_strategy_exact_url.py`

### C063 - Implement `content_hash` Strategy

- Change:
  - Add exact hash strategy over normalized `title + "\n" + body`.
- Acceptance criteria:
  - [ ] Equal normalized content returns `DUPLICATE`.
  - [ ] Different normalized content does not return `DUPLICATE`.
  - [ ] Decision metadata includes compared hash values.
- Verification:
  - `uv run pytest -q tests/dedupe/test_strategy_content_hash.py`

### C064 - Implement `title_similarity` Strategy

- Change:
  - Add RapidFuzz token-set ratio strategy with default threshold `0.92`.
  - Add short-title guard (`<3 tokens` => `ABSTAIN`).
- Acceptance criteria:
  - [ ] Above-threshold pair returns `DUPLICATE`.
  - [ ] Below-threshold pair returns non-duplicate decision.
  - [ ] Short-title cases return `ABSTAIN`.
- Verification:
  - `uv run pytest -q tests/dedupe/test_strategy_title_similarity.py`

### C065 - Implement Candidate Selection Stage

- Change:
  - Implement horizon filter + blocking keys + max candidate cap (`50`).
- Acceptance criteria:
  - [ ] Candidates outside horizon are excluded.
  - [ ] Blocking key filters reduce candidate set deterministically.
  - [ ] Candidate count never exceeds cap.
- Verification:
  - `uv run pytest -q tests/dedupe/test_candidate_selection.py`

### C066 - Implement Ordered Strategy Chain Engine

- Change:
  - Execute strategies in configured order with short-circuit semantics.
- Acceptance criteria:
  - [ ] First `DUPLICATE` short-circuits evaluation.
  - [ ] First `DISTINCT` short-circuits evaluation.
  - [ ] All-`ABSTAIN` path returns `DISTINCT(no_strategy_match)`.
- Verification:
  - `uv run pytest -q tests/dedupe/test_chain_execution.py`

### C067 - Implement Cluster Create/Add Member Flow

- Change:
  - Create new cluster for unmatched items and add memberships.
- Acceptance criteria:
  - [ ] New item without match creates exactly one cluster.
  - [ ] Membership row created for each item-cluster link.
  - [ ] Duplicate membership insertion is prevented.
- Verification:
  - `uv run pytest -q tests/dedupe/test_cluster_create.py`

### C068 - Implement Cluster Merge Flow

- Change:
  - Merge multiple matched clusters into smallest `cluster_id` target.
- Acceptance criteria:
  - [ ] All members move to target cluster in one transaction.
  - [ ] Source clusters are removed/marked merged as per schema.
  - [ ] Merge decision event is recorded.
- Verification:
  - `uv run pytest -q tests/dedupe/test_cluster_merge.py`

### C069 - Implement Representative Recompute Logic

- Change:
  - Recompute representative item using deterministic priority rules.
- Acceptance criteria:
  - [ ] Rule order matches design exactly.
  - [ ] Ties resolve by lowest `item_id`.
  - [ ] Recompute runs after merge and purge operations.
- Verification:
  - `uv run pytest -q tests/dedupe/test_representative_selection.py`

### C070 - Persist Dedupe Decision Explainability Records

- Change:
  - Store strategy-level decision metadata in `dedupe_decisions`.
- Acceptance criteria:
  - [ ] Every dedupe attempt persists decision records.
  - [ ] Record includes strategy name, outcome, reason, and score where applicable.
  - [ ] Decisions can be retrieved by item or cluster.
- Verification:
  - `uv run pytest -q tests/dedupe/test_decision_persistence.py`

---

## Phase 8: Thread API, UI, and Operational Jobs

### C071 - Implement On-Demand Thread Query Repository

- Change:
  - Build read query joining clusters, members, items, channels for timeline response.
- Acceptance criteria:
  - [ ] Results are ordered by representative `published_at` descending.
  - [ ] Query uses eager loading or explicit SQL (no lazy load path).
  - [ ] Pagination returns deterministic pages.
- Verification:
  - `uv run pytest -q tests/thread/test_thread_query.py`

### C072 - Implement `GET /thread` API

- Change:
  - Add thread endpoint with paging/filter options.
- Acceptance criteria:
  - [ ] Endpoint returns cluster-level entries with duplicate counts.
  - [ ] Supports page/size validation and bounds.
  - [ ] Protected by bearer auth.
- Verification:
  - `uv run pytest -q tests/api/test_thread_api.py`

### C073 - Implement Dedupe Decision Read API

- Change:
  - Add endpoint for explainability (`/dedupe/decisions/{item_id}` or equivalent).
- Acceptance criteria:
  - [ ] Returns full strategy decision trace for target item.
  - [ ] Unknown item returns `404`.
  - [ ] Response schema is stable and documented.
- Verification:
  - `uv run pytest -q tests/api/test_dedupe_decisions_api.py`

### C074 - Implement Minimal UI Shell (Jinja2 + HTMX + Pico)

- Change:
  - Add base template, layout, static assets, and auth-protected UI routing.
- Acceptance criteria:
  - [ ] UI renders without Node build pipeline.
  - [ ] Base template loads HTMX and CSS successfully.
  - [ ] Unauthorized UI access redirects or rejects consistently.
- Verification:
  - `uv run pytest -q tests/ui/test_shell.py`

### C075 - Implement First-Run Setup UI (Auth + Unlock)

- Change:
  - Build multi-step setup pages for unlock mode and Telegram auth.
- Acceptance criteria:
  - [ ] Setup wizard follows required step order.
  - [ ] Invalid step transition is blocked.
  - [ ] Successful flow persists account and exits setup mode.
- Verification:
  - `uv run pytest -q tests/ui/test_setup_wizard.py`

### C076 - Implement Channels + Groups UI Views

- Change:
  - Build pages/forms for channel CRUD and group assignment/override.
- Acceptance criteria:
  - [ ] User can add/edit/disable channels from UI.
  - [ ] User can create groups and assign one channel per group.
  - [ ] Group horizon override is editable and persisted.
- Verification:
  - `uv run pytest -q tests/ui/test_channels_groups_views.py`

### C077 - Implement Thread + Explainability UI Views

- Change:
  - Build merged thread page and per-item decision drill-down panel.
- Acceptance criteria:
  - [ ] Thread page shows representative item, duplicate count, sources.
  - [ ] Clicking entry shows dedupe decision details.
  - [ ] Pagination/filter controls work end-to-end.
- Verification:
  - `uv run pytest -q tests/ui/test_thread_views.py`

### C078 - Implement Notifications UI Panel

- Change:
  - Add UI for listing and acknowledging notifications.
- Acceptance criteria:
  - [ ] Notifications list sorted by recency.
  - [ ] Acknowledge action updates state in UI and DB.
  - [ ] High-severity alerts are visually distinguishable.
- Verification:
  - `uv run pytest -q tests/ui/test_notifications_view.py`

### C079 - Implement Ordered Retention Prune Job

- Change:
  - Add daily prune routine with the exact six-step order and batch size `500`.
- Acceptance criteria:
  - [ ] Job executes steps in designed order.
  - [ ] Batch size respected for raw/items deletions.
  - [ ] Cluster recomputation/empty cluster cleanup is verified.
- Verification:
  - `uv run pytest -q tests/ops/test_prune_job.py`

### C080 - Implement Nightly SQLite Backup Job

- Change:
  - Add backup job using SQLite Online Backup API and integrity check.
- Acceptance criteria:
  - [ ] Backup file created with expected naming format.
  - [ ] `PRAGMA integrity_check` passes on created backup.
  - [ ] Backup failure creates notification.
- Verification:
  - `uv run pytest -q tests/ops/test_backup_job.py`

### C081 - Implement Backup Retention Cleanup

- Change:
  - Keep last `N` backups based on dynamic setting `backup.retain_count`.
- Acceptance criteria:
  - [ ] Older backups beyond `N` are removed.
  - [ ] Newest `N` files are retained.
  - [ ] `N` changes from settings are applied without restart.
- Verification:
  - `uv run pytest -q tests/ops/test_backup_retention.py`

### C082 - Implement Graceful Shutdown Sequencing

- Change:
  - Add SIGTERM shutdown choreography: stop scheduler, drain tasks, flush writer, close clients/sessions.
- Acceptance criteria:
  - [ ] Shutdown sequence executes in required order.
  - [ ] In-flight write operations are either committed or rolled back cleanly.
  - [ ] App exits before timeout in controlled test.
- Verification:
  - `uv run pytest -q tests/ops/test_graceful_shutdown.py`

---

## Phase 9: Packaging, Deployment, and Final Validation

### C083 - Add Dockerfile for Single-Container Runtime

- Change:
  - Build production Dockerfile for Python 3.12 runtime and app entrypoint.
- Acceptance criteria:
  - [ ] `docker build` succeeds.
  - [ ] Image starts app and serves `/health`.
  - [ ] No Node/Rust toolchain required in runtime image.
- Verification:
  - `docker build -t tca:test .`
  - `docker run --rm -p 8787:8787 tca:test`

### C084 - Add Compose File with Persistent Volumes

- Change:
  - Add `docker-compose.yml` using pinned semver image tag and `/data` volume.
- Acceptance criteria:
  - [ ] Compose file uses non-`latest` image tag.
  - [ ] Volume persists DB/backups across container restart.
  - [ ] Default bind and mode env vars match design.
- Verification:
  - `docker compose config`

### C085 - Add Entrypoint Startup Order Enforcement

- Change:
  - Ensure startup order: migrations -> settings seed -> app serve.
- Acceptance criteria:
  - [ ] App refuses to serve if migration step fails.
  - [ ] First-run settings seed occurs before first request handling.
  - [ ] Startup logs clearly show step boundaries.
- Verification:
  - `uv run pytest -q tests/app/test_startup_order.py`

### C086 - Add End-to-End Smoke Test (Auth Mocked)

- Change:
  - Add integration test covering: create channel -> poll -> dedupe -> thread read.
- Acceptance criteria:
  - [ ] Smoke test passes in CI/test environment.
  - [ ] Result includes dedupe cluster and thread item output.
  - [ ] Test is deterministic and stable across runs.
- Verification:
  - `uv run pytest -q tests/integration/test_smoke_pipeline.py`

### C087 - Add End-to-End Retention/Backup Smoke Test

- Change:
  - Add integration test covering prune and backup jobs.
- Acceptance criteria:
  - [ ] Retention removes expired data and preserves non-expired.
  - [ ] Backup file created and validated.
  - [ ] Post-prune cluster invariants hold.
- Verification:
  - `uv run pytest -q tests/integration/test_retention_backup.py`

### C088 - Add API Contract Freeze for Phase 1 Endpoints

- Change:
  - Freeze OpenAPI schema for all Phase 1 routes and enforce snapshot test.
- Acceptance criteria:
  - [ ] Snapshot includes auth, channels, groups, thread, settings, notifications, jobs.
  - [ ] Contract test fails when schema drifts without snapshot update.
  - [ ] Snapshot update procedure is documented.
- Verification:
  - `uv run pytest -q tests/api/test_openapi_full_snapshot.py`

### C089 - Update README for Local Install and Security Notes

- Change:
  - Update `README.md` with current run instructions, unlock modes, and token handling.
- Acceptance criteria:
  - [ ] README includes Docker + local run paths.
  - [ ] README documents `secure-interactive` vs `auto-unlock` tradeoff.
  - [ ] README includes Telegram credential prerequisites.
- Verification:
  - Manual doc review checklist in `tests/docs/readme_checklist.md`

### C090 - Add Final Release Checklist Document

- Change:
  - Add `docs/release-checklist.md` with binary go/no-go checks aligned to design.
- Acceptance criteria:
  - [ ] Checklist covers schema, auth, ingestion, dedupe, UI, backups, and shutdown.
  - [ ] Every checklist item maps to test or manual validation step.
  - [ ] Checklist can be executed by a second engineer without implicit knowledge.
- Verification:
  - Manual checklist dry-run in local environment.

---

## Dependency Summary (High-Level)

- C001-C007 plus C003A-C003B must complete before feature development.
- C008-C018 plus C011A must complete before most API/business logic.
- C022A should complete before write-heavy API and ingest paths.
- C027-C034 must complete before Telegram login persistence.
- C049-C056 must complete before dedupe and thread behavior is meaningful.
- C057-C070 must complete before UI thread/explainability is complete.
- C079-C082 can start after schema + repositories + scheduler basics exist.

## Definition of Done for Phase 1

Phase 1 is complete only when all are true:

- C001 through C090 plus C003A, C003B, C011A, and C022A are complete.
- All automated tests referenced in this plan pass in CI.
- `docker compose up` produces a working local system matching `docs/option-a-local-design.md`.
- No open P0/P1 severity gaps against Section 22 clarifications.

---

# 🛡️ Technical Lead Review & Feedback (2026-02-15)

## Overall Assessment
The implementation plan is **highly aligned** with the architecture specified in `docs/option-a-local-design.md`. The commit-atomic approach (90 items) provides a robust framework for development, ensuring that each step is small enough to be manageable while maintaining a clear path toward the final architecture.

## 1. Architectural Alignment
- **Concurrency:** The plan correctly prioritizes SQLite WAL mode (C009) and `BEGIN IMMEDIATE` (C010). These are critical for the "Local Monolith" architecture to handle concurrent reads during ingestion.
- **Lifecycle:** The use of FastAPI lifespan hooks (C035) for Telethon client management is consistent with best practices for sharing an event loop between a web server and a long-lived MTProto connection.
- **Security:** The separation of DEK/KEK and the use of Argon2id for passphrase derivation (C031) correctly implements the high-privacy requirements.

## 2. Granularity & "Junior Survival"
- **Strengths:** The plan is granular enough that a junior developer can follow it without getting lost in "magic" implementation details. The binary acceptance criteria reduce ambiguity during PR reviews.
- **Complexity Risk:** Some verification steps (e.g., C010: "demonstrates writer lock acquisition behavior deterministically") are technically challenging to test. 
  - **Advice:** I recommend providing a `tests/conftest.py` boilerplate or a "Testing Guide" early on (Phase 0) that demonstrates how to simulate concurrent SQLite write attempts to trigger `SQLITE_BUSY`.
- **Missing Link:** Design Section 6.2 mentions a "single in-process async write queue." While `BEGIN IMMEDIATE` handles this at the DB level, the plan should explicitly include a service or utility in `tca/storage` that serializes writes to prevent unnecessary busy-retries in the app layer.

## 3. Understandability & Testability
- **Acceptance Criteria:** Most criteria are measurable and binary.
- **FTS5 Integration:** C016 and C017 are well-placed. Using Alembic for trigger creation is the correct "Ops-first" approach for SQLite's specialized features.
- **Pagination:** C052 correctly translates Telegram's complex pagination into bounded, resumable units.

## 4. Key Recommendations for Implementation
1. **Serialization Service:** Add an item to Phase 2 to implement the "Writer Queue" service mentioned in Design 6.2. This ensures that even if a junior dev adds multiple write-heavy endpoints, they all go through a single throttled path.
2. **Mocking Strategy:** Junior developers will struggle with mocking Telethon for tests. Provide a dedicated `MockTelegramClient` in Phase 0 to avoid "flaky" tests that attempt real network calls.
3. **Migration Batching:** Ensure the team understands *why* `render_as_batch=True` is used for SQLite (Section 6.5).

**Verdict:** The plan is ready for execution. It is robust, defensive, and provides excellent guardrails for a junior-heavy team.

## Feedback Integration Update (2026-02-16)

- Recommendation 1 (Serialization Service): Addressed via `C022A` in Phase 2.
- Recommendation 2 (Mocking Strategy): Addressed via `C003B` in Phase 0.
- Recommendation 3 (Migration Batching Rationale): Addressed via `C011A` in Phase 1.
- Additional testing-risk advice (`SQLITE_BUSY` deterministic testing): Addressed via `C003A` in Phase 0.
