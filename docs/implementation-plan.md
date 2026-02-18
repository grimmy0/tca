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
  - append explicit test node IDs on each completed criterion as `[Tests: tests/...::test_...]`,
  - add `Execution record` with date, commit hash, and verification summary.

## Global Quality Gate (applies to every item)

An item is commit-ready only if all are true:

- Targeted tests for the changed area pass.
- No unrelated files are modified.
- Lint/type checks for touched modules pass (`scripts/lint_strict.sh`), including:
  - execution-record SHA validation (`check_execution_record_shas.py`)
  - migration downgrade test coverage (`check_migration_downgrade_coverage.py`)
  - async broad-exception-catch detection (`check_broad_exception_catch.py`)
  - hardcoded future-year datetime literals in tests (`check_hardcoded_test_dates.py`)
- `uv run python scripts/validate_plan_criteria.py --run-tests` passes for all completed criteria.
- If API/DB behavior changes, corresponding docs or migration files are included in the same commit.

---

## Phase 0: Foundation and Project Shape

### C001 - Create Runtime Package Skeleton

- Change:
  - Create top-level package layout matching design module boundaries: `tca/api`, `tca/ui`, `tca/auth`, `tca/ingest`, `tca/normalize`, `tca/dedupe`, `tca/storage`, `tca/scheduler`, `tca/ops`.
  - Add minimal `__init__.py` files.
- Acceptance criteria:
  - [x] All listed directories exist and are importable Python packages. [Tests: tests/contracts/test_plan_traceability_contracts.py::test_tca_package_layout_exists]
  - [x] `uv run python -c "import tca"` exits `0`. [Tests: tests/contracts/test_plan_traceability_contracts.py::test_tca_importable]
  - [x] No runtime behavior beyond importability is introduced. [Tests: tests/contracts/test_plan_traceability_contracts.py::test_tca_import_has_no_root_logger_side_effects]
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
  - [x] `pyproject.toml` contains all required runtime dependencies. [Tests: tests/contracts/test_plan_traceability_contracts.py::test_runtime_dependencies_declared]
  - [x] Telethon pin is constrained to `1.42.*`. [Tests: tests/contracts/test_plan_traceability_contracts.py::test_telethon_pin_uses_142_series]
  - [x] `uv lock` completes successfully. [Tests: tests/contracts/test_plan_traceability_contracts.py::test_uv_lock_exists_and_contains_telethon]
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
  - [x] `uv run ruff check .` exits `0` on initial scaffold. [Tests: tests/contracts/test_plan_traceability_contracts.py::test_ruff_tooling_configured]
  - [x] `uv run pytest -q` exits `0` with zero or baseline tests. [Tests: tests/contracts/test_plan_traceability_contracts.py::test_pytest_tooling_configured]
  - [x] `uv run mypy tca` exits `0` for current code. [Tests: tests/contracts/test_plan_traceability_contracts.py::test_mypy_strict_tooling_configured]
- Verification:
  - `uv run ruff check .`
  - `uv run pytest -q`
  - `uv run mypy tca`
- Execution record:
  - Date: 2026-02-15
  - Commit: `37f9b32`
  - Verification summary:
    - `ruff` passed (after removing print in `main.py`).
    - `pytest` passed (with `pytest-asyncio` added to handle `asyncio_mode` warning).
    - `mypy` passed with `Success: no issues found in 10 source files`.

### C003A - Add Shared Testing Harness and SQLite Concurrency Guide

- Change:
  - Add `tests/conftest.py` with reusable fixtures for async DB session setup and deterministic concurrency tests.
  - Add `docs/testing-guide.md` with a focused section showing how to reproduce and assert `SQLITE_BUSY` scenarios in tests.
- Acceptance criteria:
  - [x] `tests/conftest.py` exposes fixtures used by storage tests and does not perform network calls. [Tests: tests/contracts/test_plan_traceability_contracts.py::test_shared_sqlite_fixture_exists_without_network_calls]
  - [x] Guide includes one runnable example for concurrent write contention and expected assertion pattern. [Tests: tests/contracts/test_plan_traceability_contracts.py::test_testing_guide_documents_sqlite_busy_reproduction]
  - [x] C010 tests use shared fixtures/harness instead of ad-hoc concurrency setup. [Tests: tests/contracts/test_plan_traceability_contracts.py::test_storage_concurrency_test_uses_shared_fixture]
- Verification:
  - `uv run pytest -q tests/storage/test_begin_immediate.py`
  - `rg -n "SQLITE_BUSY|concurrency" docs/testing-guide.md tests/conftest.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `87cac1b`
  - Verification summary:
    - `uv run pytest -q tests/storage/test_begin_immediate.py` passed (`1 passed`).
    - `rg -n "SQLITE_BUSY|concurrency" docs/testing-guide.md tests/conftest.py` matched fixture and guide references.
    - `tests/storage/test_begin_immediate.py` uses shared fixture `sqlite_writer_pair` from `tests/conftest.py`.

### C003B - Add `MockTelegramClient` Test Double

- Change:
  - Add `tests/mocks/mock_telegram_client.py` implementing the minimal Telethon-like surface needed by Phase 1 tests.
  - Add fixture wiring to inject `MockTelegramClient` into API/auth/scheduler tests.
- Acceptance criteria:
  - [x] Tests for Telegram flows can run without any real Telethon network interaction. [Tests: tests/api/test_telegram_auth_start.py::test_auth_start_mock, tests/ingest/test_flood_wait.py::test_flood_wait_mock]
  - [x] Mock supports deterministic success/failure scripting for OTP, flood-wait, and message fetch paths. [Tests: tests/api/test_telegram_auth_start.py::test_auth_start_mock, tests/ingest/test_flood_wait.py::test_flood_wait_mock]
  - [x] At least one auth test and one ingest test are switched to the shared mock. [Tests: tests/api/test_telegram_auth_start.py::test_auth_start_mock, tests/ingest/test_flood_wait.py::test_flood_wait_mock]
- Verification:
  - `uv run pytest -q tests/api/test_telegram_auth_start.py`
  - `uv run pytest -q tests/ingest/test_flood_wait.py`
  - `uv run pytest -q tests/mocks/test_mock_telegram_client.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `623dd59`
  - Verification summary:
    - Created `MockTelegramClient` with deterministic scripting for OTP (`send_code_request`), flood wait, and message fetch (`iter_messages` / `get_messages`) paths.
    - Added `mock_tg_client` fixture to `tests/conftest.py`.
    - Implemented service-level tests in `tests/api/test_telegram_auth_start.py` and `tests/ingest/test_flood_wait.py` so auth/ingest integration paths are exercised through injected client interfaces.
    - Added `tests/mocks/test_mock_telegram_client.py` verifying `run_until_disconnected` exits deterministically and does not hang tests.

### C004 - Introduce Centralized App Settings Model

- Change:
  - Implement typed settings loader for env vars: `TCA_DB_PATH`, `TCA_BIND`, `TCA_MODE`, `TCA_LOG_LEVEL`, `TCA_SECRET_FILE`.
  - Define defaults from design.
- Acceptance criteria:
  - [x] Settings object loads with defaults when env vars are absent. [Tests: tests/settings/test_settings_loader.py::test_load_settings_uses_design_defaults_when_env_absent]
  - [x] Invalid values (for mode/log level) raise deterministic validation error. [Tests: tests/settings/test_settings_loader.py::test_load_settings_rejects_invalid_mode_and_log_level]
  - [x] Unit tests cover default and invalid env cases. [Tests: tests/settings/test_settings_loader.py::test_load_settings_uses_design_defaults_when_env_absent, tests/settings/test_settings_loader.py::test_load_settings_rejects_invalid_mode_and_log_level]
- Verification:
  - `uv run pytest -q tests/settings`
- Execution record:
  - Date: 2026-02-16
  - Commit: `674501a`
  - Verification summary:
    - Implemented typed static env loader in `tca/config/settings.py` with deterministic validation errors for `TCA_MODE` and `TCA_LOG_LEVEL`.
    - Added defaults for mode/bind/db path aligned with design and optional `TCA_SECRET_FILE` handling.
    - Added `tests/settings/test_settings_loader.py` covering default resolution and invalid mode/log-level failures.

### C005 - Add Structured Logging Bootstrap

- Change:
  - Add logging initializer used by app startup.
  - Include request correlation ID field placeholder.
- Acceptance criteria:
  - [x] App startup emits structured logs at configured level. [Tests: tests/logging/test_logging_init.py::test_json_formatter_outputs_valid_json]
  - [x] Log level changes with `TCA_LOG_LEVEL`. [Tests: tests/logging/test_logging_init.py::test_init_logging_sets_level]
  - [x] Unit test asserts logger configuration behavior. [Tests: tests/logging/test_logging_init.py::test_json_formatter_includes_extra_fields]
- Verification:
  - `uv run pytest -q tests/logging`
- Execution record:
  - Date: 2026-02-16
  - Commit: `06b482d`
  - Verification summary:
    - Implemented `tca/config/logging.py` with `JSONFormatter` for structured logging.
    - Added `correlation_id` ContextVar for async tracing.
    - Added unit tests in `tests/logging/test_logging_init.py` covering level setting, JSON output, correlation ID inclusion, and extra field merging.

### C006 - Add App Factory and Lifespan Hooks

- Change:
  - Implement FastAPI app factory with lifespan context manager.
  - Register startup/shutdown hook stubs for DB, Telethon manager, scheduler.
- Acceptance criteria:
  - [x] App can start and stop cleanly in tests without real Telegram calls. [Tests: tests/app/test_lifespan.py::test_app_lifespan_triggers_logging_and_hooks_once]
  - [x] Lifespan hooks run once per test app lifecycle. [Tests: tests/app/test_lifespan.py::test_app_lifespan_triggers_logging_and_hooks_once]
  - [x] Missing startup dependency fails fast with clear error. [Tests: tests/app/test_lifespan.py::test_lifespan_fails_fast_on_missing_dependency_container, tests/app/test_lifespan.py::test_lifespan_fails_fast_on_missing_named_dependency]
- Verification:
  - `uv run pytest -q tests/app/test_lifespan.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `ee95ac6`
  - Verification summary:
    - Implemented `create_app` factory and `lifespan` context manager in `tca/api/app.py`.
    - Integrated settings loading and logging initialization in the factory.
    - Added `tests/app/test_lifespan.py` verifying that lifespan events (startup/shutdown) are correctly triggered and logged during `TestClient` context usage.

### C007 - Add `/health` Endpoint

- Change:
  - Implement unauthenticated `GET /health` route.
  - Return deterministic JSON payload with status and timestamp.
- Acceptance criteria:
  - [x] `GET /health` returns `200` without auth header. [Tests: tests/api/test_health.py::test_get_health_returns_ok]
  - [x] Response schema is stable and documented. [Tests: tests/api/test_health.py::test_health_openapi_schema_is_explicit_and_stable]
  - [x] Health route remains accessible when bearer auth middleware is later enabled. [Tests: tests/api/test_health.py::test_get_health_returns_ok]
- Verification:
  - `uv run pytest -q tests/api/test_health.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `e727886`
  - Verification summary:
    - Implemented `tca/api/routes/health.py` returning `{"status": "ok", "timestamp": ...}`.
    - Registered the health router in `tca/api/app.py`.
    - Added `tests/api/test_health.py` verifying `200 OK` and schema.

---

## Phase 1: Database Engine, Migrations, and Schema

### C008 - Add SQLAlchemy Engine/Session Wiring

- Change:
  - Implement async engine/session factory using SQLite path from settings.
  - Provide separate session helpers for read and write paths.
- Acceptance criteria:
  - [x] Engine initializes against configured DB path. [Tests: tests/storage/test_engine.py::test_engine_initializes_against_configured_db_path]
  - [x] Session factory can execute a simple `SELECT 1`. [Tests: tests/storage/test_engine.py::test_session_factory_can_execute_select_one]
  - [x] Test fixture can create and teardown session cleanly. [Tests: tests/storage/test_engine.py::test_fixture_supports_clean_read_and_write_session_lifecycle]
- Verification:
  - `uv run pytest -q tests/storage/test_engine.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `f389bc6`
  - Verification summary:
    - Added `tca/storage/db.py` with typed async read/write engine and session factory helpers.
    - Added `tests/storage/test_engine.py` for DB path wiring, `SELECT 1`, and fixture lifecycle coverage.

### C009 - Apply Mandatory SQLite PRAGMA Settings

- Change:
  - Add engine-connect event handlers enforcing WAL, synchronous, FK, busy_timeout pragmas.
- Acceptance criteria:
  - [x] Runtime PRAGMA values match design on every fresh connection. [Tests: tests/storage/test_sqlite_pragmas.py::test_runtime_pragmas_match_design_on_fresh_connection]
  - [x] Test verifies each PRAGMA value exactly. [Tests: tests/storage/test_sqlite_pragmas.py::test_pragmas_match_exact_values_for_writer_connection]
  - [x] Regression test fails if any PRAGMA is removed. [Tests: tests/storage/test_sqlite_pragmas.py::test_pragmas_are_reapplied_on_each_new_connection]
- Verification:
  - `uv run pytest -q tests/storage/test_sqlite_pragmas.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `f0646b2`
  - Verification summary:
    - Added SQLite connect-event PRAGMA enforcement in `tca/storage/db.py`.
    - Added `tests/storage/test_sqlite_pragmas.py` covering exact PRAGMA values and repeated-connection regression checks.

### C010 - Enforce `BEGIN IMMEDIATE` for Write Transactions

- Change:
  - Add SQLAlchemy begin event hook for writer connections to issue `BEGIN IMMEDIATE`.
- Acceptance criteria:
  - [x] Writer transaction begins in `IMMEDIATE` mode. [Tests: tests/storage/test_begin_immediate.py::test_writer_transactions_emit_begin_immediate]
  - [x] Read transactions remain non-writing and unaffected. [Tests: tests/storage/test_begin_immediate.py::test_read_transactions_remain_unaffected_by_begin_immediate]
  - [x] Test demonstrates writer lock acquisition behavior deterministically. [Tests: tests/storage/test_begin_immediate.py::test_writer_lock_acquisition_is_deterministic_with_begin_immediate, tests/storage/test_begin_immediate.py::test_begin_immediate_surfaces_sqlite_busy_with_second_writer]
- Verification:
  - `uv run pytest -q tests/storage/test_begin_immediate.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `b9075cd`
  - Verification summary:
    - Added writer-engine `BEGIN IMMEDIATE` transaction hook in `tca/storage/db.py`.
    - Expanded `tests/storage/test_begin_immediate.py` to verify writer begin mode, read-path behavior under writer lock, and deterministic lock contention outcomes.

### C011 - Initialize Alembic with SQLite Batch Mode

- Change:
  - Add Alembic environment and configure `render_as_batch=True`.
  - Link migration configuration notes to `docs/migration-policy.md`.
- Acceptance criteria:
  - [x] `alembic upgrade head` works on empty DB. [Tests: tests/migrations/test_alembic_setup.py::test_alembic_upgrade_head_works_on_empty_db]
  - [x] Alembic config clearly enables batch mode. [Tests: tests/migrations/test_alembic_setup.py::test_alembic_batch_mode_is_enabled_in_env_configuration]
  - [x] Migration command is invokable from project root. [Tests: tests/migrations/test_alembic_setup.py::test_alembic_current_command_invokable_from_project_root]
- Verification:
  - `uv run alembic upgrade head`
  - `uv run alembic current`
- Execution record:
  - Date: 2026-02-16
  - Commit: `04e19d7`
  - Verification summary:
    - Initialized Alembic project files (`alembic.ini`, `alembic/env.py`, templates) from repository root.
    - Configured `render_as_batch=True` in both offline and online migration contexts.
    - Linked migration policy requirements to `docs/migration-policy.md`.
    - Added `tests/migrations/test_alembic_setup.py` validating upgrade/current invocations and batch-mode configuration.

### C011A - Add Migration Policy Note for SQLite Batch Mode

- Change:
  - Add `docs/migration-policy.md` explaining why `render_as_batch=True` is mandatory for SQLite and when to use batch migrations.
  - Include a short migration checklist (pre-checks, lock considerations, rollback expectations).
- Acceptance criteria:
  - [x] Policy document explicitly references SQLite `ALTER TABLE` limitations and project batch-mode requirement. [Tests: tests/docs/test_migration_policy_doc.py::test_policy_references_sqlite_alter_table_and_batch_requirement]
  - [x] Checklist is concise and directly usable in migration PRs. [Tests: tests/docs/test_migration_policy_doc.py::test_policy_includes_concise_migration_checklist]
  - [x] C011 references this document in implementation notes. [Tests: tests/docs/test_migration_policy_doc.py::test_c011_section_references_migration_policy_document]
- Verification:
  - `rg -n "render_as_batch|SQLite|ALTER TABLE" docs/migration-policy.md docs/implementation-plan.md`
- Execution record:
  - Date: 2026-02-16
  - Commit: `45dca17`
  - Verification summary:
    - Added `docs/migration-policy.md` with explicit SQLite `ALTER TABLE` limitations and `render_as_batch=True` policy.
    - Added concise migration checklist covering pre-checks, lock scope, and rollback expectations.
    - Added `tests/docs/test_migration_policy_doc.py` enforcing policy-document content and C011 linkage.

### C012 - Create Base Migration: Accounts, Channels, Groups

- Change:
  - Add tables: `telegram_accounts`, `telegram_channels`, `channel_groups`, `channel_group_members`, `channel_state`.
  - Include group membership uniqueness (single group per channel).
- Acceptance criteria:
  - [x] All tables exist after migration. [Tests: tests/migrations/test_base_schema_groups.py::test_base_group_tables_exist_after_migration]
  - [x] `channel_group_members.channel_id` unique constraint exists. [Tests: tests/migrations/test_base_schema_groups.py::test_channel_group_members_channel_id_has_unique_constraint]
  - [x] FK relationships resolve correctly. [Tests: tests/migrations/test_base_schema_groups.py::test_group_schema_foreign_keys_resolve_correctly]
- Verification:
  - `uv run pytest -q tests/migrations/test_base_schema_groups.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `d93e6e9`
  - Verification summary:
    - Added Alembic revision `70bbc5b6d2f1` creating `telegram_accounts`, `telegram_channels`, `channel_groups`, `channel_group_members`, and `channel_state`.
    - Enforced one-group-per-channel membership with unique constraint on `channel_group_members.channel_id`.
    - Added migration tests validating table creation, membership uniqueness, and foreign-key relationships.

### C013 - Create Base Migration: Content and Dedupe Tables

- Change:
  - Add tables: `raw_messages`, `items`, `dedupe_clusters`, `dedupe_members`, `dedupe_decisions`.
  - Include `items.raw_message_id` nullable FK with `ON DELETE SET NULL`.
- Acceptance criteria:
  - [x] `items.raw_message_id` FK delete action is `SET NULL`. [Tests: tests/migrations/test_content_dedupe_schema.py::test_items_raw_message_id_fk_uses_set_null_on_delete]
  - [x] `items(channel_id, message_id)` uniqueness exists. [Tests: tests/migrations/test_content_dedupe_schema.py::test_items_channel_message_uniqueness_exists]
  - [x] `dedupe_members` uniqueness exists for `(cluster_id, item_id)`. [Tests: tests/migrations/test_content_dedupe_schema.py::test_dedupe_members_cluster_item_uniqueness_exists]
- Verification:
  - `uv run pytest -q tests/migrations/test_content_dedupe_schema.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `d434237`
  - Verification summary:
    - Added Alembic revision `5f8b0d1e2a44` creating `raw_messages`, `items`, `dedupe_clusters`, `dedupe_members`, and `dedupe_decisions`.
    - Implemented `items.raw_message_id` foreign key with `ON DELETE SET NULL`, plus uniqueness on `raw_messages(channel_id, message_id)` and `items(channel_id, message_id)`.
    - Added migration tests validating FK delete behavior and uniqueness constraints for `items` and `dedupe_members`.

### C014 - Create Base Migration: Ops Tables

- Change:
  - Add tables: `ingest_errors`, `notifications`, `settings`.
  - Add `settings.key` unique constraint.
- Acceptance criteria:
  - [x] All ops/config tables exist. [Tests: tests/migrations/test_ops_schema.py::test_ops_config_tables_exist_after_migration]
  - [x] `settings.key` is unique. [Tests: tests/migrations/test_ops_schema.py::test_settings_key_uniqueness_exists]
  - [x] `ingest_errors` has required stage and timestamp fields. [Tests: tests/migrations/test_ops_schema.py::test_ingest_errors_has_required_stage_and_timestamp_fields, tests/migrations/test_ops_schema.py::test_ingest_errors_stage_constraint_rejects_unknown_stage]
- Verification:
  - `uv run pytest -q tests/migrations/test_ops_schema.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `0588d64`
  - Verification summary:
    - Added Alembic revision `9c2a8f6d0f7b` creating `ingest_errors`, `notifications`, and `settings`.
    - Enforced uniqueness on `settings.key`.
    - Added migration tests validating ops/config table creation, `settings.key` uniqueness, and required `ingest_errors` stage/timestamp columns.

### C015 - Add Required Secondary Indexes

- Change:
  - Add all Phase 1 indexes from design (published_at/hash/indexes/group indexes/error indexes).
- Acceptance criteria:
  - [x] Each index defined in design is present in DB metadata. [Tests: tests/migrations/test_indexes.py::test_phase1_indexes_from_design_exist_in_metadata]
  - [x] Missing index causes test failure. [Tests: tests/migrations/test_indexes.py::test_phase1_index_assertion_fails_when_required_index_is_missing]
  - [x] Query explain snapshots include index usage for representative read paths. [Tests: tests/migrations/test_indexes.py::test_representative_read_path_query_plans_use_expected_indexes]
- Verification:
  - `uv run pytest -q tests/migrations/test_indexes.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `c0157c9`
  - Verification summary:
    - Added Alembic revision `c2f9c1e5a7b3` to create required Phase 1 secondary indexes on `items`, `dedupe_members`, `dedupe_clusters`, and `ingest_errors`.
    - Added migration tests asserting every design-defined Phase 1 index signature is present in SQLite metadata.
    - Added query-plan snapshot checks confirming representative read paths use the new secondary indexes.

### C016 - Add FTS5 External-Content Table Migration

- Change:
  - Create FTS5 virtual table linked to `items` as external content.
- Acceptance criteria:
  - [x] FTS5 table exists and is queryable with `MATCH`. [Tests: tests/migrations/test_fts_table.py::test_fts_table_exists_and_supports_match_queries]
  - [x] Migration is reversible. [Tests: tests/migrations/test_fts_table.py::test_fts_table_is_removed_when_downgrading_to_c015]
  - [x] FTS migration runs on SQLite without manual intervention. [Tests: tests/migrations/test_fts_table.py::test_fts_migration_runs_on_sqlite_without_manual_intervention]
- Verification:
  - `uv run pytest -q tests/migrations/test_fts_table.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `0805fee1db06b84b20b5854a2e9ab47306d4fe09`
  - Verification summary:
    - Added Alembic revision `8f3a7b0c1d2e` creating `items_fts` as an FTS5 external-content virtual table over `items(title, body)`.
    - Added migration tests verifying `MATCH` queryability, downgrade removal back to `c2f9c1e5a7b3`, and SQLite `alembic upgrade head` execution.

### C017 - Add FTS5 Trigger Migration

- Change:
  - Add insert/update/delete triggers to keep FTS index synchronized with `items`.
- Acceptance criteria:
  - [x] Insert into `items` appears in FTS results. [Tests: tests/migrations/test_fts_triggers.py::test_insert_into_items_appears_in_fts_results]
  - [x] Update modifies FTS searchable text. [Tests: tests/migrations/test_fts_triggers.py::test_update_modifies_fts_searchable_text]
  - [x] Delete removes FTS hit. [Tests: tests/migrations/test_fts_triggers.py::test_delete_removes_fts_hit]
- Verification:
  - `uv run pytest -q tests/migrations/test_fts_triggers.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `d5fc8ad70976eec238b7ea15d333d708eb3e507c`
  - Verification summary:
    - Added Alembic revision `a1f6e7c9d2b4` creating `items_fts_ai`, `items_fts_au`, and `items_fts_ad` triggers to synchronize insert/update/delete changes from `items` into `items_fts`.
    - Added migration tests validating insert visibility in `MATCH` results, update replacement of searchable terms, and delete removal from FTS hits.

### C018 - Add Migration Runner in App Startup Path

- Change:
  - Ensure startup executes `alembic upgrade head` before serving API.
- Acceptance criteria:
  - [x] On empty DB, app starts and schema is current. [Tests: tests/app/test_startup_migrations.py::test_startup_migrations_upgrade_empty_db_to_head]
  - [x] On current DB, startup is idempotent. [Tests: tests/app/test_startup_migrations.py::test_startup_migrations_are_idempotent_on_current_db]
  - [x] API does not accept requests before migration success. [Tests: tests/app/test_startup_migrations.py::test_startup_migration_failure_blocks_api_startup]
- Verification:
  - `uv run pytest -q tests/app/test_startup_migrations.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `d99e7e5c111f5a87a05623f9d7b952eceb023ce2`
  - Verification summary:
    - Added `tca/storage/migrations.py` and wired `create_app()` default DB dependency to run `alembic -c alembic.ini upgrade head` during startup before API serving.
    - Added `tests/app/test_startup_migrations.py` to validate empty DB upgrade to head, startup idempotency on current DB, and fail-fast startup behavior when migration execution errors.
    - Verified with `uv run pytest -q tests/app/test_startup_migrations.py` (3 passed) and regression check `uv run pytest -q tests/app/test_lifespan.py` (4 passed).

---

## Phase 2: Repository Layer and Config Surface

### C019 - Implement Settings Repository

- Change:
  - CRUD helpers for `settings` table with typed conversions.
- Acceptance criteria:
  - [x] Can create/read/update by `key`. [Tests: tests/storage/test_settings_repo.py::test_create_read_and_update_by_key]
  - [x] Duplicate key insertion fails deterministically. [Tests: tests/storage/test_settings_repo.py::test_duplicate_key_insert_fails_deterministically]
  - [x] JSON values preserve type fidelity. [Tests: tests/storage/test_settings_repo.py::test_json_values_preserve_type_fidelity]
- Verification:
  - `uv run pytest -q tests/storage/test_settings_repo.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `7e711580c5dc095cdfbd133503c48ce53ff7d28e`
  - Verification summary:
    - Added `tca/storage/settings_repo.py` with typed `create/get_by_key/update` helpers for `settings` keyed access, deterministic duplicate-key errors, and JSON encode/decode validation.
    - Added `tests/storage/test_settings_repo.py` covering by-key CRUD, deterministic duplicate insertion failure, and JSON type-fidelity round trips.
    - Verified with `uv run pytest -q tests/storage/test_settings_repo.py` (`3 passed in 0.16s`), plus targeted lint/type checks for touched module paths.

### C020 - Seed Default Dynamic Settings on First Boot

- Change:
  - Add bootstrap routine writing default settings only when keys absent.
- Acceptance criteria:
  - [x] First boot inserts all design default keys. [Tests: tests/settings/test_seed_defaults.py::test_first_boot_inserts_all_design_default_keys]
  - [x] Second boot does not overwrite modified values. [Tests: tests/settings/test_seed_defaults.py::test_second_boot_does_not_overwrite_modified_values]
  - [x] Missing single key is backfilled without touching others. [Tests: tests/settings/test_seed_defaults.py::test_missing_single_key_is_backfilled_without_touching_others]
- Verification:
  - `uv run pytest -q tests/settings/test_seed_defaults.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `97b8ef8293efb90fa1ce78628384193f066f1cec`
  - Verification summary:
    - Added `tca/storage/settings_seed.py` with design default dynamic setting keys and an idempotent `seed_default_dynamic_settings` routine that inserts only absent keys.
    - Wired startup lifecycle to run settings seeding after migrations by extending `StartupDependencies` in `tca/api/app.py` with `SettingsSeedDependency`.
    - Added `tests/settings/test_seed_defaults.py` to verify first-boot seeding, second-boot non-overwrite behavior, and single-key backfill without mutating other rows.
    - Verified with `uv run pytest -q tests/settings/test_seed_defaults.py` (`3 passed in 1.59s`) and startup regression checks `uv run pytest -q tests/app/test_lifespan.py tests/app/test_startup_migrations.py` (`8 passed in 1.54s`).

### C021 - Implement Channel Group Repositories

- Change:
  - Add data access layer for group CRUD and membership operations.
- Acceptance criteria:
  - [x] Group create/update/delete works. [Tests: tests/storage/test_channel_groups_repo.py::test_group_create_update_delete_works]
  - [x] Channel cannot be assigned to multiple groups. [Tests: tests/storage/test_channel_groups_repo.py::test_channel_cannot_be_assigned_to_multiple_groups]
  - [x] Removing group cleans memberships. [Tests: tests/storage/test_channel_groups_repo.py::test_removing_group_cleans_memberships]
- Verification:
  - `uv run pytest -q tests/storage/test_channel_groups_repo.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `1560869b5cc4b5fea203e7d2125c077071a7c734`
  - Verification summary:
    - Added `tca/storage/channel_groups_repo.py` with typed channel-group CRUD and membership helpers, including deterministic one-group-per-channel enforcement via `ChannelAlreadyAssignedToGroupError`.
    - Exported repository symbols via `tca/storage/__init__.py` for downstream API/service integration.
    - Added `tests/storage/test_channel_groups_repo.py` validating group create/update/delete flows, duplicate channel assignment rejection, and membership cleanup when deleting a group.
    - Verified with `uv run pytest -q tests/storage/test_channel_groups_repo.py` (`3 passed in 0.17s`).

### C022 - Implement Channels Repository with Soft-Delete Fields

- Change:
  - Add repository for channel create/update/enable/disable operations.
- Acceptance criteria:
  - [x] Channel can be disabled without row deletion. [Tests: tests/storage/test_channels_repo.py::test_disable_channel_soft_delete_without_row_deletion]
  - [x] Disabled channels are excluded from active query helper. [Tests: tests/storage/test_channels_repo.py::test_list_active_channels_excludes_disabled_channels]
  - [x] Re-enable restores active status. [Tests: tests/storage/test_channels_repo.py::test_enable_channel_restores_active_status]
- Verification:
  - `uv run pytest -q tests/storage/test_channels_repo.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `4f637b488c0e5b77633631783488d45222ace4fc`
  - Verification summary:
    - Added `tca/storage/channels_repo.py` with typed `telegram_channels` create/update/get operations, soft-disable/enable transitions, and an active-only list helper.
    - Exported `ChannelsRepository`, `ChannelRecord`, and channel decode/error types from `tca/storage/__init__.py` for downstream usage.
    - Added `tests/storage/test_channels_repo.py` covering create/update behavior plus C022 acceptance paths for non-destructive disable, active-query filtering, and re-enable restoration.
    - Verified with `uv run pytest -q tests/storage/test_channels_repo.py` (`4 passed in 0.17s`) and regression check `uv run pytest -q tests/storage/test_channel_groups_repo.py` (`5 passed in 0.18s`).

### C022A - Implement Single Writer Queue Service

- Change:
  - Add `tca/storage/writer_queue.py` to serialize write operations through one in-process async queue.
  - Add API and scheduler integration points to route all mutating DB operations through the queue abstraction.
- Acceptance criteria:
  - [x] Only one queued write job is executed at a time (verified by concurrency test). [Tests: tests/storage/test_writer_queue.py::test_writer_queue_executes_only_one_job_at_a_time]
  - [x] Concurrent write submissions are processed FIFO and all produce deterministic completion/error results. [Tests: tests/storage/test_writer_queue.py::test_writer_queue_processes_fifo_and_preserves_result_error_outcomes]
  - [x] At least one API write path and one ingest write path are switched to writer-queue execution. [Tests: tests/api/test_settings_api.py::test_put_settings_writes_execute_through_app_writer_queue, tests/ingest/test_raw_upsert.py::test_raw_upsert_uses_writer_queue_for_ingest_write_serialization]
- Verification:
  - `uv run pytest -q tests/storage/test_writer_queue.py`
  - `uv run pytest -q tests/api/test_settings_api.py`
  - `uv run pytest -q tests/ingest/test_raw_upsert.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `f9786a44c84212705006cab0e014c5c889157a16`
  - Verification summary:
    - Added `tests/storage/test_writer_queue.py` to verify single in-flight writer execution under concurrent submissions plus FIFO deterministic success/error completion behavior.
    - Added queue-backed settings API write coverage in `tests/api/test_settings_api.py`, asserting `/settings/{key}` mutations execute via app writer queue and preserve deterministic update results.
    - Added ingest write-queue integration helper `upsert_raw_message(...)` in `tca/ingest/service.py` and exported it via `tca/ingest/__init__.py`, with coverage in `tests/ingest/test_raw_upsert.py`.
    - Verified with `uv run pytest -q tests/storage/test_writer_queue.py` (`2 passed in 0.37s`), `uv run pytest -q tests/api/test_settings_api.py` (`1 passed in 0.80s`), and `uv run pytest -q tests/ingest/test_raw_upsert.py` (`2 passed in 0.19s`).

### C023 - Add Config Resolution Service

- Change:
  - Implement runtime config resolution contract:
    - static env values,
    - dynamic settings rows,
    - group-specific horizon override.
- Acceptance criteria:
  - [x] Global horizon comes from settings key. [Tests: tests/settings/test_resolution.py::test_global_horizon_comes_from_dynamic_settings_key]
  - [x] Group override wins over global when present. [Tests: tests/settings/test_resolution.py::test_group_override_wins_over_global_horizon]
  - [x] Missing setting falls back to seeded default value. [Tests: tests/settings/test_resolution.py::test_missing_horizon_setting_falls_back_to_seeded_default]
- Verification:
  - `uv run pytest -q tests/settings/test_resolution.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `77304c710c7928f0d8c128f5d27be3e100d2e6c8`
  - Verification summary:
    - Added `tca/config/resolution.py` with `ConfigResolutionService`, combining static app settings exposure with dynamic horizon resolution from settings rows and seeded-default fallback.
    - Added `tests/settings/test_resolution.py` to validate global horizon lookup from `dedupe.default_horizon_minutes`, group override precedence, and fallback to the seeded default when the key is missing.
    - Exported config resolution symbols from `tca/config/__init__.py` for downstream API and service integration.
    - Verified with `uv run pytest -q tests/settings/test_resolution.py` (`3 passed in 0.15s`) plus lint/type checks `uv run ruff check tca/config/resolution.py tca/config/__init__.py tests/settings/test_resolution.py` and `uv run mypy tca/config/resolution.py`.

### C024 - Add Settings API (Read + Update Allowed Keys)

- Change:
  - Add API endpoints for dynamic settings read/update with allowlist.
- Acceptance criteria:
  - [x] Unknown keys are rejected with `400`. [Tests: tests/api/test_settings_api.py::test_unknown_setting_keys_are_rejected_with_bad_request]
  - [x] Allowed keys update immediately and persist. [Tests: tests/api/test_settings_api.py::test_allowed_setting_key_updates_immediately_and_persists_across_restart]
  - [x] Response returns effective value after write. [Tests: tests/api/test_settings_api.py::test_put_setting_returns_effective_value_after_write]
- Verification:
  - `uv run pytest -q tests/api/test_settings_api.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `ad71acfe64f155fd96d54cab0a6733dcc1008f18`
  - Verification summary:
    - Updated `tca/api/routes/settings.py` to enforce a dynamic-settings allowlist for both read and write operations, rejecting unknown keys with `400`.
    - Added `GET /settings/{key}` and updated `PUT /settings/{key}` to return effective persisted values with seeded-default fallback semantics.
    - Extended `tests/api/test_settings_api.py` with coverage for unknown key rejection, immediate update + restart persistence, and effective write response behavior.
    - Verified with `uv run pytest -q tests/api/test_settings_api.py` (`4 passed in 3.35s`), `uv run ruff check tca/api/routes/settings.py tests/api/test_settings_api.py`, and `uv run mypy tca/api/routes/settings.py`.

### C025 - Add Channel Group API Endpoints

- Change:
  - Implement all endpoints from design section 12.4.
- Acceptance criteria:
  - [x] CRUD operations return expected status codes. [Tests: tests/api/test_channel_groups_api.py::test_channel_group_crud_endpoints_return_expected_status_codes]
  - [x] Membership add/remove endpoint updates join table. [Tests: tests/api/test_channel_groups_api.py::test_channel_group_membership_put_and_delete_update_join_table]
  - [x] Group horizon override can be set and cleared. [Tests: tests/api/test_channel_groups_api.py::test_channel_group_horizon_override_can_be_set_and_cleared]
- Verification:
  - `uv run pytest -q tests/api/test_channel_groups_api.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `3bf7b207ebd0bfdfe86ee15b125b39c15277cd49`
  - Verification summary:
    - Added `tca/api/routes/channel_groups.py` implementing design section 12.4 endpoints: group list/create/update/delete and membership put/delete, with writer-queue execution for mutating operations.
    - Extended `tca/storage/channel_groups_repo.py` with `list_groups()` to support `GET /channel-groups` ordering and response payloads.
    - Registered channel-group routes in `tca/api/app.py` and added API coverage in `tests/api/test_channel_groups_api.py` for CRUD status codes, membership join-table add/remove behavior, and horizon override set/clear semantics.
    - Verified with `uv run pytest -q tests/api/test_channel_groups_api.py` (`3 passed in 2.07s`), plus `uv run ruff check tca/api/routes/channel_groups.py tca/api/app.py tca/storage/channel_groups_repo.py tests/api/test_channel_groups_api.py` and `uv run mypy tca/api/routes/channel_groups.py tca/api/app.py tca/storage/channel_groups_repo.py tests/api/test_channel_groups_api.py`.

### C026 - Add OpenAPI Contract Snapshot for Config/Groups

- Change:
  - Freeze and version endpoint schema snapshot for settings and group endpoints.
- Acceptance criteria:
  - [x] Schema snapshot includes all new endpoints and payload fields. [Tests: tests/api/test_openapi_snapshot.py::test_openapi_snapshot_includes_config_and_group_endpoints_and_fields]
  - [x] CI test fails on unreviewed API contract drift. [Tests: tests/api/test_openapi_snapshot.py::test_openapi_snapshot_matches_committed_contract]
  - [x] Snapshot update process documented. [Tests: tests/docs/test_openapi_snapshot_doc.py::test_testing_guide_documents_openapi_snapshot_update_process]
- Verification:
  - `uv run pytest -q tests/api/test_openapi_snapshot.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `de708e868673ac66721a6b5315d925e6cbdfa216`
  - Verification summary:
    - Added `tests/api/test_openapi_snapshot.py`, which extracts the `/settings/{key}` and `/channel-groups*` OpenAPI subset plus referenced component schemas and compares it against a committed snapshot, failing with a diff on unreviewed contract drift.
    - Added snapshot artifact `tests/api/snapshots/config_groups_openapi_snapshot.json` covering settings and channel-group endpoints with their request/response payload schemas.
    - Documented the snapshot refresh workflow in `docs/testing-guide.md` and added `tests/docs/test_openapi_snapshot_doc.py` to enforce presence of the documented update commands.
    - Verified with `uv run pytest -q tests/api/test_openapi_snapshot.py` (`2 passed in 1.11s`) and `uv run pytest -q tests/docs/test_openapi_snapshot_doc.py` (`1 passed in 0.02s`).

---

## Phase 3: API Security and Secret Handling

### C027 - Implement Bootstrap Bearer Token Generation

- Change:
  - Generate `secrets.token_urlsafe(32)` token on first run.
  - Persist only SHA-256 digest.
- Acceptance criteria:
  - [x] Plain token is never written to DB. [Tests: tests/auth/test_bootstrap_token.py::test_bootstrap_token_plain_value_is_never_persisted_to_db]
  - [x] Token is shown once at bootstrap output path. [Tests: tests/auth/test_bootstrap_token.py::test_bootstrap_token_is_written_once_to_configured_output_path]
  - [x] Re-start does not rotate token automatically. [Tests: tests/auth/test_bootstrap_token.py::test_restart_does_not_rotate_bootstrap_token_automatically]
- Verification:
  - `uv run pytest -q tests/auth/test_bootstrap_token.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `86649394c085a96e0cc54cb1d98c7acf1152352d`
  - Verification summary:
    - Added `tca/auth/bootstrap_token.py` with startup bootstrap flow that generates a first-run bearer token via `secrets.token_urlsafe(32)`, persists only its SHA-256 digest in `settings`, and writes the plain token once to a bootstrap output file path.
    - Wired bootstrap token initialization into app lifespan startup via `StartupDependencies.auth` in `tca/api/app.py`, keeping token generation independent from later auth middleware enforcement work.
    - Added `tests/auth/test_bootstrap_token.py` covering digest-only DB persistence, one-time token output emission, and restart behavior that preserves the original token digest without automatic rotation.
    - Verified with `uv run pytest -q tests/auth/test_bootstrap_token.py` (`3 passed in 2.54s`), plus `uv run ruff check tca/auth/bootstrap_token.py tca/auth/__init__.py tca/api/app.py tests/auth/test_bootstrap_token.py tests/app/test_lifespan.py`, `uv run pytest -q tests/auth/test_bootstrap_token.py tests/app/test_lifespan.py`, and `uv run mypy tca/auth/bootstrap_token.py tca/auth/__init__.py tca/api/app.py tests/auth/test_bootstrap_token.py tests/app/test_lifespan.py`.

### C028 - Implement Bearer Auth Middleware

- Change:
  - Enforce bearer auth on all non-health routes.
  - Compare token digest with constant-time compare.
- Acceptance criteria:
  - [x] Unauthenticated protected route returns `401`. [Tests: tests/api/test_bearer_auth.py::test_unauthenticated_protected_route_returns_401]
  - [x] Invalid token returns `401`. [Tests: tests/api/test_bearer_auth.py::test_invalid_token_returns_401]
  - [x] Valid token returns `200` for protected route. [Tests: tests/api/test_bearer_auth.py::test_valid_token_returns_200_for_protected_route]
- Verification:
  - `uv run pytest -q tests/api/test_bearer_auth.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `67baced`
  - Verification summary:
    - Added `tca/api/bearer_auth.py` with bearer-token validation that loads the stored bootstrap token digest from `settings`, computes the presented token digest, and verifies using constant-time `secrets.compare_digest`.
    - Wired bearer auth enforcement in `tca/api/app.py` by applying `Depends(require_bearer_auth)` to all non-health routers while leaving `GET /health` unauthenticated.
    - Added `tests/api/test_bearer_auth.py` covering `401` for missing auth, `401` for invalid token, and `200` for valid token on a protected settings route.
    - Verified with `uv run pytest -q tests/api/test_bearer_auth.py` (`3 passed in 1.09s`), `uv run ruff check tca/api/bearer_auth.py tca/api/app.py tests/api/test_bearer_auth.py`, `uv run mypy tca/api/bearer_auth.py tca/api/app.py tests/api/test_bearer_auth.py`, and `uv run python scripts/validate_plan_criteria.py`.

### C029 - Add CORS Allowlist Enforcement

- Change:
  - Implement default-deny CORS with explicit allowlist config.
- Acceptance criteria:
  - [x] No CORS headers when origin not allowlisted. [Tests: tests/api/test_cors.py::test_origin_not_allowlisted_receives_no_cors_headers]
  - [x] Allowlisted origin receives expected CORS headers. [Tests: tests/api/test_cors.py::test_allowlisted_origin_receives_expected_cors_headers]
  - [x] Behavior is covered by API tests. [Tests: tests/api/test_cors.py::test_origin_not_allowlisted_receives_no_cors_headers, tests/api/test_cors.py::test_allowlisted_origin_receives_expected_cors_headers]
- Verification:
  - `uv run pytest -q tests/api/test_cors.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `89251c9`
  - Verification summary:
    - Added `TCA_CORS_ALLOW_ORIGINS` static settings support in `tca/config/settings.py` with tuple parsing and empty-by-default behavior to enforce default-deny CORS.
    - Wired CORS middleware setup in `tca/api/app.py` so headers are emitted only for configured allowlisted origins.
    - Added `tests/api/test_cors.py` validating no CORS headers for non-allowlisted origins and expected CORS headers for allowlisted origins.
    - Verified with `uv run pytest -q tests/api/test_cors.py` (`2 passed in 0.95s`), `uv run pytest -q tests/settings/test_settings_loader.py` (`4 passed in 0.12s`), `uv run ruff check tca/config/settings.py tca/api/app.py tests/api/test_cors.py tests/settings/test_settings_loader.py`, and `uv run mypy tca/config/settings.py tca/api/app.py tests/api/test_cors.py tests/settings/test_settings_loader.py`.

### C030 - Implement Envelope Encryption Utilities

- Change:
  - Implement DEK generation + data encryption/decryption helpers.
  - Implement KEK wrapping/unwrapping flow.
- Acceptance criteria:
  - [x] Encrypt/decrypt round trip returns exact original bytes. [Tests: tests/auth/test_encryption_utils.py::test_encrypt_decrypt_round_trip_returns_exact_original_bytes]
  - [x] Decrypt with wrong key fails deterministically. [Tests: tests/auth/test_encryption_utils.py::test_decrypt_with_wrong_key_fails_deterministically]
  - [x] Ciphertext payload includes version metadata. [Tests: tests/auth/test_encryption_utils.py::test_ciphertext_payload_includes_version_metadata]
- Verification:
  - `uv run pytest -q tests/auth/test_encryption_utils.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `f285f9f`
  - Verification summary:
    - Added `tca/auth/encryption_utils.py` implementing DEK generation, AES key wrap/unwrap helpers for KEK flow, and AES-GCM envelope encrypt/decrypt helpers.
    - Standardized envelope payload serialization as UTF-8 JSON with base64-encoded binary fields and explicit `version` metadata for forward-compatible schema evolution.
    - Added `tests/auth/test_encryption_utils.py` covering byte-exact encrypt/decrypt round-trip, deterministic wrong-KEK decryption failure via `EnvelopeDecryptionError`, and payload version metadata presence.
    - Verified with `uv run pytest -q tests/auth/test_encryption_utils.py`, `uv run ruff check tca/auth/encryption_utils.py tca/auth/__init__.py tests/auth/test_encryption_utils.py`, and `uv run mypy tca/auth/encryption_utils.py tca/auth/__init__.py tests/auth/test_encryption_utils.py`.

### C031 - Implement Argon2id KEK Derivation

- Change:
  - Implement passphrase KDF using Argon2id with design parameters.
- Acceptance criteria:
  - [x] KDF parameters match design values exactly. [Tests: tests/auth/test_kdf.py::test_kdf_parameters_match_design_values_exactly]
  - [x] Same passphrase+salt yields deterministic key. [Tests: tests/auth/test_kdf.py::test_same_passphrase_and_salt_yield_deterministic_key]
  - [x] Different salt yields different key. [Tests: tests/auth/test_kdf.py::test_different_salt_yields_different_derived_key]
- Verification:
  - `uv run pytest -q tests/auth/test_kdf.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `4279257526265848002ddf6032927fbfca08d1b6`
  - Verification summary:
    - Added `tca/auth/kdf.py` with Argon2id KEK derivation using the design baseline parameters: memory 64 MiB (`65536` KiB), iterations `3`, parallelism `1`, and salt length `16` bytes.
    - Added `tests/auth/test_kdf.py` covering exact Argon2id parameter wiring, deterministic same passphrase+salt derivation, and different-salt output divergence.
    - Verified with `uv run pytest -q tests/auth/test_kdf.py`.

### C032 - Implement Startup Unlock Modes

- Change:
  - Add `secure-interactive` and `auto-unlock` startup behavior.
- Acceptance criteria:
  - [x] Secure mode requires unlock action before sensitive operations. [Tests: tests/auth/test_unlock_modes.py::test_secure_interactive_mode_requires_explicit_unlock_action_before_sensitive_operations]
  - [x] Auto-unlock mode reads mounted secret file. [Tests: tests/auth/test_unlock_modes.py::test_auto_unlock_mode_reads_secret_from_mounted_file]
  - [x] Missing secret in auto mode fails startup with actionable error. [Tests: tests/auth/test_unlock_modes.py::test_auto_unlock_mode_missing_secret_fails_startup_with_actionable_error]
- Verification:
  - `uv run pytest -q tests/auth/test_unlock_modes.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `e46e649`
  - Verification summary:
    - Added `tca/auth/unlock_modes.py` implementing startup unlock-mode initialization, in-memory unlock state, explicit unlock action helpers, and actionable startup errors for missing/unreadable/empty auto-unlock secrets.
    - Added `AuthStartupDependency` and wired `tca/api/app.py` default auth lifecycle dependency to initialize unlock modes before bootstrap bearer token setup.
    - Added `tests/auth/test_unlock_modes.py` covering secure-interactive lock enforcement, auto-unlock mounted-secret file loading, and startup failure with actionable guidance when auto-unlock secret is missing.
    - Verified with `uv run pytest -q tests/auth/test_unlock_modes.py`, plus regression checks `uv run pytest -q tests/app/test_lifespan.py tests/auth/test_bootstrap_token.py tests/api/test_bearer_auth.py` and lint checks `uv run ruff check tca/auth/unlock_modes.py tca/auth/__init__.py tca/api/app.py tests/auth/test_unlock_modes.py`.

### C033 - Persist Encrypted Telegram Session Material

- Change:
  - Add persistence logic for encrypted session blob in `telegram_accounts`.
- Acceptance criteria:
  - [x] Stored session data is encrypted (not plaintext StringSession). [Tests: tests/auth/test_session_storage.py::test_stored_session_data_is_encrypted_not_plaintext_stringsession]
  - [x] Session round-trip through DB decrypts correctly. [Tests: tests/auth/test_session_storage.py::test_session_round_trip_through_db_decrypts_correctly]
  - [x] Incorrect KEK prevents session load. [Tests: tests/auth/test_session_storage.py::test_incorrect_kek_prevents_session_load]
- Verification:
  - `uv run pytest -q tests/auth/test_session_storage.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `424981c`
  - Verification summary:
    - Added `tca/auth/session_storage.py` with `TelegramSessionStorage` methods that encrypt `StringSession` data using the existing envelope helper before writing `telegram_accounts.session_encrypted`, and decrypt on read.
    - Added deterministic account-missing and payload-shape error handling for session persistence/load paths to keep failures explicit for repository callers.
    - Added `tests/auth/test_session_storage.py` covering ciphertext-at-rest assertions, DB round-trip decrypt fidelity, and wrong-KEK failure via `EnvelopeDecryptionError`.
    - Verified with `uv run pytest -q tests/auth/test_session_storage.py`.

### C034 - Implement Crash-Safe Key Rotation Metadata

- Change:
  - Add rotation state tracking table/fields and row-version markers.
- Acceptance criteria:
  - [x] Rotation state persists progress. [Tests: tests/auth/test_key_rotation_resume.py::test_rotation_state_persists_progress]
  - [x] Interrupted rotation can resume at next pending row. [Tests: tests/auth/test_key_rotation_resume.py::test_interrupted_rotation_resumes_at_next_pending_row]
  - [x] Completion state only set when all targeted rows rotated. [Tests: tests/auth/test_key_rotation_resume.py::test_completion_state_only_set_after_all_rows_rotated]
- Verification:
  - `uv run pytest -q tests/auth/test_key_rotation_resume.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `4b0ca98`
  - Verification summary:
    - Added key rotation metadata repository with persisted progress and completion tracking.
    - Added rotation metadata schema (row-level key version + rotation state table).
    - Added tests covering progress persistence, resume behavior, and completion gating.
    - Verified with `uv run pytest -q tests/auth/test_key_rotation_resume.py`.

---

## Phase 4: Telegram Auth Flow and Account Lifecycle

### C035 - Add Telethon Client Manager

- Change:
  - Implement shared Telethon client manager in app state.
  - Lifecycle hooks connect/disconnect clients.
- Acceptance criteria:
  - [x] Client manager initializes during app startup. [Tests: tests/telegram/test_client_manager.py::test_client_manager_connects_on_startup]
  - [x] Clients disconnect on app shutdown. [Tests: tests/telegram/test_client_manager.py::test_client_manager_disconnects_on_shutdown]
  - [x] No per-request client creation occurs. [Tests: tests/telegram/test_client_manager.py::test_client_manager_does_not_create_client_on_get]
- Verification:
  - `uv run pytest -q tests/telegram/test_client_manager.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `7541452`
  - Verification summary:
    - Added Telethon client manager with explicit startup/shutdown hooks and a registry that avoids implicit per-request creation.
    - Wired the manager into FastAPI startup dependencies and exported Telegram client management types.
    - Added client manager lifecycle tests covering startup connect, shutdown disconnect, and missing-client access behavior.
    - Verified with `uv run pytest -q tests/telegram/test_client_manager.py`.

### C036 - Add Auth Session State Storage for Login Wizard

- Change:
  - Add temporary auth session state model for phone/code/password steps.
- Acceptance criteria:
  - [x] Auth session state has expiry. [Tests: tests/auth/test_auth_session_state.py::test_auth_session_state_has_expiry]
  - [x] Expired session is rejected. [Tests: tests/auth/test_auth_session_state.py::test_expired_session_is_rejected]
  - [x] Parallel auth sessions for different users are isolated. [Tests: tests/auth/test_auth_session_state.py::test_parallel_auth_sessions_are_isolated]
- Verification:
  - `uv run pytest -q tests/auth/test_auth_session_state.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `93860eb`
  - Verification summary:
    - Added `auth_session_state` storage table migration with expiry tracking for login wizard state.
    - Implemented auth session state repository with expiry enforcement and session isolation behavior.
    - Added auth session state tests covering expiry persistence, expired-session rejection, and parallel session isolation.
    - Verified with `uv run pytest -q tests/auth/test_auth_session_state.py`.

### C037 - Implement `POST /auth/telegram/start`

- Change:
  - Accept `api_id`, `api_hash`, phone number; request OTP via Telethon.
- Acceptance criteria:
  - [x] Valid payload returns auth session token/id. [Tests: tests/api/test_telegram_auth_start.py::test_auth_start_returns_session_id]
  - [x] Invalid API credentials return controlled error. [Tests: tests/api/test_telegram_auth_start.py::test_auth_start_invalid_api_credentials_return_controlled_error]
  - [x] No OTP or credential secrets are logged. [Tests: tests/api/test_telegram_auth_start.py::test_auth_start_does_not_log_secrets]
- Verification:
  - `uv run pytest -q tests/api/test_telegram_auth_start.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `b6ee4b7`
  - Verification summary:
    - Added `POST /auth/telegram/start` to request OTP and create auth session state.
    - Added API tests for success responses, invalid credentials, and log redaction.
    - Verified with `uv run pytest -q tests/api/test_telegram_auth_start.py`.

### C038 - Implement `POST /auth/telegram/verify-code`

- Change:
  - Verify OTP and transition auth session state.
- Acceptance criteria:
  - [x] Correct code advances to authenticated or password-required state. [Tests: tests/api/test_telegram_auth_verify_code.py::test_verify_code_advances_to_authenticated_state, tests/api/test_telegram_auth_verify_code.py::test_verify_code_requires_password_updates_status]
  - [x] Wrong code returns deterministic error response. [Tests: tests/api/test_telegram_auth_verify_code.py::test_verify_code_wrong_code_returns_deterministic_error]
  - [x] Replayed/expired code path returns failure. [Tests: tests/api/test_telegram_auth_verify_code.py::test_verify_code_replayed_or_expired_session_returns_failure]
- Verification:
  - `uv run pytest -q tests/api/test_telegram_auth_verify_code.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `513c071`
  - Verification summary:
    - Added `POST /auth/telegram/verify-code` to validate OTP codes and advance auth session status.
    - Added API tests for authenticated/password-required transitions, invalid codes, and replayed/expired sessions.
    - Verified with `uv run pytest -q tests/api/test_telegram_auth_verify_code.py`.

### C039 - Implement `POST /auth/telegram/verify-password`

- Change:
  - Complete 2FA password step when required.
- Acceptance criteria:
  - [x] Correct password finalizes login. [Tests: tests/api/test_telegram_auth_verify_password.py::test_verify_password_finalizes_login]
  - [x] Wrong password returns retryable error. [Tests: tests/api/test_telegram_auth_verify_password.py::test_verify_password_wrong_password_returns_retryable_error]
  - [x] Endpoint rejects calls when password step not required. [Tests: tests/api/test_telegram_auth_verify_password.py::test_verify_password_rejects_when_step_not_required]
- Verification:
  - `uv run pytest -q tests/api/test_telegram_auth_verify_password.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `54526ff`
  - Verification summary:
    - Added `POST /auth/telegram/verify-password` for password-required sessions.
    - Added API tests for correct password, wrong password, and invalid session status.
    - Verified with `uv run pytest -q tests/api/test_telegram_auth_verify_password.py`.

### C040 - Persist StringSession After Successful Login

- Change:
  - Convert authenticated Telethon session to StringSession and persist encrypted.
- Acceptance criteria:
  - [x] Post-login account row created/updated in `telegram_accounts`. [Tests: tests/telegram/test_stringsession_persistence.py::test_stringsession_persisted_and_reused]
  - [x] Session value is encrypted at rest. [Tests: tests/telegram/test_stringsession_persistence.py::test_stringsession_persisted_and_reused]
  - [x] Later client initialization can reuse saved session without OTP. [Tests: tests/telegram/test_stringsession_persistence.py::test_stringsession_persisted_and_reused]
- Verification:
  - `uv run pytest -q tests/telegram/test_stringsession_persistence.py`
- Execution record:
  - Date: 2026-02-17
  - Commit: `14cce6f`
  - Verification summary:
    - Guarded OTP/password verification when sensitive operations are locked.
    - Added locked-mode coverage and StringSession test session setup.
    - Verified with `uv run pytest -q tests/api/test_telegram_auth_verify_code.py tests/api/test_telegram_auth_verify_password.py tests/telegram/test_stringsession_persistence.py`.

### C041 - Implement Registration/Login Failure Notifications

- Change:
  - On registration block/auth failure, write notification with actionable message.
- Acceptance criteria:
  - [x] Expected failure classes produce `auth_registration_blocked` or related notification. [Tests: tests/notifications/test_auth_notifications.py::test_auth_start_blocked_registration_creates_notification, tests/notifications/test_auth_notifications.py::test_auth_verify_code_failed_login_creates_notification]
  - [x] Notification severity is set correctly. [Tests: tests/notifications/test_auth_notifications.py::test_auth_start_blocked_registration_creates_notification, tests/notifications/test_auth_notifications.py::test_auth_verify_code_failed_login_creates_notification]
  - [x] Notification payload includes retry guidance. [Tests: tests/notifications/test_auth_notifications.py::test_auth_start_blocked_registration_creates_notification, tests/notifications/test_auth_notifications.py::test_auth_verify_code_failed_login_creates_notification]
- Verification:
  - `uv run pytest -q tests/notifications/test_auth_notifications.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `ec461171`
  - Verification summary:
    - Added auth failure notifications with retry guidance and severity.
    - Persisted auth registration/login failures into notifications storage.
    - Verified with `uv run pytest -q tests/notifications/test_auth_notifications.py`.

### C042 - Add Account Pause/Resume Flags

- Change:
  - Add account-level pause field and resume operation used by risk controls.
- Acceptance criteria:
  - [x] Paused account channels are excluded from scheduler selection. [Tests: tests/ingest/test_account_pause_flags.py::test_scheduler_selection_excludes_paused_accounts]
  - [x] Resume operation clears pause state. [Tests: tests/ingest/test_account_pause_flags.py::test_resume_clears_pause_state]
  - [x] Pause reason is persisted. [Tests: tests/ingest/test_account_pause_flags.py::test_pause_reason_persisted]
- Verification:
  - `uv run pytest -q tests/ingest/test_account_pause_flags.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `0485734`
  - Verification summary:
    - Added account pause/resume storage and schedulable channel filtering.
    - Persisted pause reasons on accounts and added pause flags migration.
    - Verified with `uv run pytest -q tests/ingest/test_account_pause_flags.py`.

---

## Phase 5: Channel and Source Management APIs

### C043 - Implement Channels CRUD API

- Change:
  - Add API for create/list/update channels (Telegram-only schema).
- Acceptance criteria:
  - [x] Channel create validates required Telegram identifiers. [Tests: tests/api/test_channels_crud.py::test_channel_create_validates_required_telegram_identifiers]
  - [x] List endpoint returns only caller-visible channels. [Tests: tests/api/test_channels_crud.py::test_list_channels_returns_only_enabled_rows]
  - [x] Update endpoint persists polling-related fields. [Tests: tests/api/test_channels_crud.py::test_patch_channel_persists_polling_state_updates]
- Verification:
  - `uv run pytest -q tests/api/test_channels_crud.py`
- Execution record:
  - Date: 2026-02-17
  - Commit: `6490536`
  - Verification summary:
    - Added channels CRUD routes with polling state persistence and visibility filtering.
    - Added channel state repository plus merge migration for Alembic heads.
    - Verified with `uv run pytest -q tests/api/test_channels_crud.py`.

### C044 - Implement Channel Soft-Delete API Behavior

- Change:
  - Implement default delete mode as disable/hide only.
- Acceptance criteria:
  - [x] `DELETE /channels/{id}` marks channel disabled. [Tests: tests/api/test_channel_soft_delete.py::test_delete_channel_marks_disabled]
  - [x] Historical items remain queryable. [Tests: tests/api/test_channel_soft_delete.py::test_delete_channel_preserves_historical_items]
  - [x] Disabled channel no longer scheduled for polling. [Tests: tests/api/test_channel_soft_delete.py::test_delete_channel_excludes_scheduler_selection]
- Verification:
  - `uv run pytest -q tests/api/test_channel_soft_delete.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `a351f66`
  - Verification summary:
    - Added channel DELETE route that disables channels via the writer queue.
    - Preserved historical items and scheduler exclusion behavior in tests.
    - Verified with `uv run pytest -q tests/api/test_channel_soft_delete.py`.

### C045 - Implement Channel `purge=true` Delete Path

- Change:
  - Add hard delete flow with cascade recomputation hooks.
- Acceptance criteria:
  - [x] Raw/items rows for channel are removed. [Tests: tests/api/test_channel_purge_delete.py::test_purge_delete_removes_channel_items_and_raw_messages_and_records_audit]
  - [x] Affected clusters are recomputed and empty clusters removed. [Tests: tests/api/test_channel_purge_delete.py::test_purge_delete_recomputes_clusters_and_removes_empty]
  - [x] Audit record is stored. [Tests: tests/api/test_channel_purge_delete.py::test_purge_delete_removes_channel_items_and_raw_messages_and_records_audit]
- Verification:
  - `uv run pytest -q tests/api/test_channel_purge_delete.py`
 - Execution record:
  - Date: 2026-02-16
  - Commit: `8ec8556`
  - Verification summary:
    - Added purge delete path that removes channel rows, recomputes affected clusters, and logs an audit notification.
    - Added purge delete tests covering data removal, cluster recompute, and audit logging.
    - Verified with `uv run pytest -q tests/api/test_channel_purge_delete.py`.

### C046 - Add Manual Poll Trigger Endpoint

- Change:
  - Implement `POST /jobs/poll-now/{channel_id}`.
- Acceptance criteria:
  - [x] Trigger enqueues poll job for active channel. [Tests: tests/api/test_poll_now.py::test_poll_now_enqueues_job_for_active_channel]
  - [x] Disabled/paused channel returns deterministic rejection. [Tests: tests/api/test_poll_now.py::test_poll_now_rejects_disabled_channel, tests/api/test_poll_now.py::test_poll_now_rejects_paused_channel]
  - [x] Endpoint response includes job correlation ID. [Tests: tests/api/test_poll_now.py::test_poll_now_enqueues_job_for_active_channel]
- Verification:
  - `uv run pytest -q tests/api/test_poll_now.py`
- Execution record:
  - Date: 2026-02-16
  - Commit: `1eea34c`
  - Verification summary:
    - Added poll-now API endpoint that enqueues poll jobs and rejects disabled/paused channels.
    - Added poll jobs queue table and repository plus manual poll tests.
    - Verified with `uv run pytest -q tests/api/test_poll_now.py`.

### C047 - Add API Endpoint to Read Notifications

- Change:
  - Implement notifications read/list endpoint for UI alerts.
- Acceptance criteria:
  - [x] Endpoint returns notifications sorted by recency. [Tests: tests/api/test_notifications_api.py::test_list_notifications_returns_recent_first]
  - [x] Supports filtering by severity/type. [Tests: tests/api/test_notifications_api.py::test_list_notifications_filters_by_severity_and_type]
  - [x] Protected by bearer auth. [Tests: tests/api/test_notifications_api.py::test_list_notifications_requires_bearer_auth]
- Verification:
  - `uv run pytest -q tests/api/test_notifications_api.py`
- Execution record:
  - Date: 2026-02-17
  - Commit: `50f1b9c`
  - Verification summary:
    - Added notifications list endpoint with recency ordering and filtering.
    - Protected notifications list route with bearer auth.
    - Verified with `uv run pytest -q tests/api/test_notifications_api.py`.

### C048 - Add API Endpoint to Acknowledge Notifications

- Change:
  - Implement acknowledge action to mark notification resolved/read.
- Acceptance criteria:
  - [x] Notification acknowledge updates state atomically. [Tests: tests/api/test_notifications_ack.py::test_acknowledge_notification_updates_state_atomically]
  - [x] Re-acknowledging is idempotent. [Tests: tests/api/test_notifications_ack.py::test_acknowledge_notification_is_idempotent]
  - [x] Response includes updated notification state. [Tests: tests/api/test_notifications_ack.py::test_acknowledge_notification_updates_state_atomically]
- Verification:
  - `uv run pytest -q tests/api/test_notifications_ack.py`
- Execution record:
  - Date: 2026-02-17
  - Commit: `00bcb3e`
  - Verification summary:
    - Added acknowledge endpoint and repository update for notifications.
    - Responses now return acknowledged state on repeated calls.
    - Verified with `uv run pytest -q tests/api/test_notifications_ack.py`.

---

## Phase 6: Scheduler, Polling, and Ingestion

### C049 - Implement Scheduler Core Loop

- Change:
  - Add scheduler service selecting eligible channels by interval.
- Acceptance criteria:
  - [x] Eligible channels are selected by `next_run_at` logic. [Tests: tests/scheduler/test_core_loop.py::test_next_run_at_selection_uses_last_success_at]
  - [x] Disabled channels are excluded. [Tests: tests/scheduler/test_core_loop.py::test_disabled_channels_are_excluded_from_scheduler]
  - [x] Scheduler loop can start/stop cleanly. [Tests: tests/scheduler/test_core_loop.py::test_scheduler_service_starts_and_stops_cleanly]
- Verification:
  - `uv run pytest -q tests/scheduler/test_core_loop.py`
- Execution record:
  - Date: 2026-02-17
  - Commit: `8f41495`
  - Verification summary:
    - Added scheduler core loop with next-run selection logic and poll job enqueueing.
    - Wired scheduler lifecycle dependency into app startup/shutdown.
    - Added scheduler core loop tests and verified with `uv run pytest -q tests/scheduler/test_core_loop.py`.

### C050 - Add Polling Interval + Jitter Computation

- Change:
  - Implement default interval and +/-20% jitter policy.
- Acceptance criteria:
  - [x] Computed next run time lies within jitter bounds. [Tests: tests/scheduler/test_jitter.py::test_next_run_at_within_jitter_bounds]
  - [x] Jitter is deterministic under seeded RNG in tests. [Tests: tests/scheduler/test_jitter.py::test_jitter_is_deterministic_with_seeded_rng]
  - [x] Configurable poll interval from settings is honored. [Tests: tests/scheduler/test_jitter.py::test_poll_interval_resolves_from_settings]
- Verification:
  - `uv run pytest -q tests/scheduler/test_jitter.py`
- Execution record:
  - Date: 2026-02-17
  - Commit: `f4d35a8`
  - Verification summary:
    - Added jittered next-run computation with deterministic RNG handling.
    - Resolved scheduler poll interval from dynamic settings.
    - Added jitter tests and verified with `uv run pytest -q tests/scheduler/test_jitter.py`.

### C051 - Implement Channel Cursor Persistence

- Change:
  - Persist and read cursor JSON (`last_message_id`, `next_offset_id`, `last_polled_at`).
- Acceptance criteria:
  - [x] Cursor updates after successful poll. [Tests: tests/ingest/test_cursor_state.py::test_cursor_updates_after_successful_poll]
  - [x] Cursor read on next run resumes from previous state. [Tests: tests/ingest/test_cursor_state.py::test_cursor_read_resumes_from_previous_state]
  - [x] Cursor schema validation rejects malformed payload. [Tests: tests/ingest/test_cursor_state.py::test_cursor_schema_validation_rejects_malformed_payload]
- Verification:
  - `uv run pytest -q tests/ingest/test_cursor_state.py`

### C052 - Implement Bounded Pagination Logic

- Change:
  - Enforce `max_pages_per_poll` and `max_messages_per_poll`.
- Acceptance criteria:
  - [x] Poll stops when either limit is reached. [Tests: tests/ingest/test_pagination_bounds.py::test_pagination_stops_on_page_limit, tests/ingest/test_pagination_bounds.py::test_pagination_stops_on_message_limit_and_sets_offset]
  - [x] Unfinished pagination stores `next_offset_id`. [Tests: tests/ingest/test_pagination_bounds.py::test_pagination_stops_on_message_limit_and_sets_offset]
  - [x] Next run continues from stored offset. [Tests: tests/ingest/test_pagination_bounds.py::test_pagination_resumes_from_stored_offset]
- Verification:
  - `uv run pytest -q tests/ingest/test_pagination_bounds.py`
- Execution record:
  - Date: 2026-02-17
  - Commit: `cd4108c`
  - Verification summary:
    - `uv run pytest -q tests/ingest/test_pagination_bounds.py` passed (`3 passed`).

### C053 - Implement Flood-Wait Handling

- Change:
  - Parse FloodWait, pause channel until resume timestamp, store event.
- Acceptance criteria:
  - [x] Flood wait exception marks channel paused with exact resume time. [Tests: tests/ingest/test_flood_wait.py::test_flood_wait_marks_channel_paused_until_resume_time]
  - [x] Paused channels are skipped by scheduler. [Tests: tests/scheduler/test_core_loop.py::test_paused_channels_are_skipped_by_scheduler]
  - [x] Notification emitted for significant pause durations. [Tests: tests/ingest/test_flood_wait.py::test_flood_wait_emits_notification_for_significant_pause]
- Verification:
  - `uv run pytest -q tests/ingest/test_flood_wait.py`
- Execution record:
  - Date: 2026-02-17
  - Commit: `d2f0e1a`
  - Verification summary:
    - `uv run pytest -q tests/ingest/test_flood_wait.py` passed (`4 passed`).
    - `uv run pytest -q tests/scheduler/test_core_loop.py` passed (`5 passed`).

### C054 - Implement Account Risk Escalation

- Change:
  - Detect repeated flood/auth failures and pause entire account.
- Acceptance criteria:
  - [x] Repeated threshold breaches trigger account pause. [Tests: tests/ingest/test_account_risk_escalation.py::test_account_risk_escalation_pauses_account_on_repeated_breaches]
  - [x] High-severity notification is emitted once per pause event. [Tests: tests/ingest/test_account_risk_escalation.py::test_account_risk_escalation_emits_notification_once]
  - [x] Polling does not continue until explicit resume. [Tests: tests/ingest/test_account_risk_escalation.py::test_account_risk_escalation_blocks_schedulable_channels_until_resume]
- Verification:
  - `uv run pytest -q tests/ingest/test_account_risk_escalation.py`
- Execution record:
  - Date: 2026-02-17
  - Commit: `599f10c`
  - Verification summary:
    - `uv run pytest -q tests/ingest/test_account_risk_escalation.py` passed (`3 passed, 3 warnings`).

### C055 - Implement Ingest Error Capture

- Change:
  - Persist errors in `ingest_errors` with stage/code/message/payload_ref.
- Acceptance criteria:
  - [x] All error stages map to allowed enum values. [Tests: tests/ingest/test_error_capture.py::test_ingest_error_stage_mapping_matches_allowed_values]
  - [x] Error rows include non-null timestamp. [Tests: tests/ingest/test_error_capture.py::test_ingest_error_rows_include_non_null_timestamp]
  - [x] Ingest pipeline continues after recoverable errors. [Tests: tests/ingest/test_error_capture.py::test_ingest_pipeline_continues_after_recoverable_errors]
- Verification:
  - `uv run pytest -q tests/ingest/test_error_capture.py`
- Execution record:
  - Date: 2026-02-17
  - Commit: `eed5bfb`
  - Verification summary:
    - `uv run pytest -q tests/ingest/test_error_capture.py` passed (`3 passed`)

### C056 - Implement Raw Message Upsert Logic

- Change:
  - Upsert `raw_messages` by `(channel_id, message_id)` with latest payload.
- Acceptance criteria:
  - [x] Duplicate ingest of same message updates existing row, not inserts duplicate. [Tests: tests/ingest/test_raw_upsert.py::test_raw_upsert_updates_existing_row_without_duplicate]
  - [x] Raw payload is replaced with latest version. [Tests: tests/ingest/test_raw_upsert.py::test_raw_upsert_replaces_payload_with_latest_version]
  - [x] Unique constraint violations do not crash poll loop. [Tests: tests/ingest/test_raw_upsert.py::test_raw_upsert_handles_unique_constraint_conflict]
- Verification:
  - `uv run pytest -q tests/ingest/test_raw_upsert.py`
- Execution record:
  - Date: 2026-02-17
  - Commit: `c2410ba`
  - Verification summary:
    - `uv run pytest -q tests/ingest/test_raw_upsert.py` passed (`5 passed in 0.18s`).

---

## Phase 7: Normalization, Dedupe, and Clustering

### C057 - Implement Normalized Item Upsert

- Change:
  - Upsert `items` keyed by `(channel_id, message_id)`.
  - Maintain `raw_message_id` linkage when available.
- Acceptance criteria:
  - [x] First ingest inserts item; re-ingest updates same row. [Tests: tests/normalize/test_items_upsert.py::test_item_upsert_updates_existing_row_without_duplicate]
  - [x] `raw_message_id` set on insert and maintained on update. [Tests: tests/normalize/test_items_upsert.py::test_item_upsert_preserves_raw_message_id_on_update]
  - [x] Deleting linked raw row sets `items.raw_message_id` to `NULL`. [Tests: tests/normalize/test_items_upsert.py::test_item_upsert_nulls_raw_message_id_on_delete]
- Verification:
  - `uv run pytest -q tests/normalize/test_items_upsert.py`
- Execution record:
  - Date: 2026-02-18
  - Commit: `220af6cf63324faa1460d8fc7c77535d7061a733`
  - Verification summary:
    - `uv run pytest -q tests/normalize/test_items_upsert.py` passed.

### C058 - Implement URL Canonicalization Utility

- Change:
  - Normalize URLs and strip tracking query params.
- Acceptance criteria:
  - [x] Known tracking params are removed. [Tests: tests/normalize/test_url_canonicalization.py::test_known_tracking_params_are_removed]
  - [x] Semantically equivalent URLs normalize identically. [Tests: tests/normalize/test_url_canonicalization.py::test_semantically_equivalent_urls_normalize_identically]
  - [x] Non-URL text input is handled safely. [Tests: tests/normalize/test_url_canonicalization.py::test_non_url_text_input_is_handled_safely]
- Verification:
  - `uv run pytest -q tests/normalize/test_url_canonicalization.py`
- Execution record:
  - Date: 2026-02-18
  - Commit: `dd41c6d3231759621a560fe3711ac0f4cbc4b56a`
  - Verification summary:
    - `uv run pytest -q tests/normalize/test_url_canonicalization.py` passed.

### C059 - Implement Hash Normalization Pipeline

- Change:
  - Implement hash pipeline exactly as specified for `content_hash` generation.
- Acceptance criteria:
  - [x] Same semantic input yields same normalized hash input. [Tests: tests/normalize/test_hash_normalization.py::test_same_semantic_input_yields_same_normalized_hash_input]
  - [x] Non-alphanumeric collapse behavior matches spec. [Tests: tests/normalize/test_hash_normalization.py::test_non_alphanumeric_collapse_behavior_matches_spec]
  - [x] Snapshot tests lock normalization outputs. [Tests: tests/normalize/test_hash_normalization.py::test_snapshot_locks_hash_normalization_outputs]
- Verification:
  - `uv run pytest -q tests/normalize/test_hash_normalization.py`
- Execution record:
  - Date: 2026-02-18
  - Commit: `c546e1b94d09682b0f524b8a3855021f70f99689`
  - Verification summary:
    - `uv run pytest -q tests/normalize/test_hash_normalization.py` passed.

### C060 - Implement Similarity Normalization Pipeline

- Change:
  - Implement similarity pipeline preserving token boundaries.
- Acceptance criteria:
  - [x] Whitespace boundaries are preserved for tokenization. [Tests: tests/normalize/test_similarity_normalization.py::test_whitespace_boundaries_are_preserved_for_tokenization]
  - [x] Tracking params/wrappers are removed. [Tests: tests/normalize/test_similarity_normalization.py::test_tracking_params_and_wrappers_are_removed]
  - [x] Snapshot tests prove divergence from hash pipeline where expected. [Tests: tests/normalize/test_similarity_normalization.py::test_snapshot_locks_similarity_and_hash_divergence]
- Verification:
  - `uv run pytest -q tests/normalize/test_similarity_normalization.py`
- Execution record:
  - Date: 2026-02-18
  - Commit: `f3c06d4aa4124ce82f6cf490e4bfb8aea7799a01`
  - Verification summary:
    - `uv run pytest -q tests/normalize/test_similarity_normalization.py` passed.

### C061 - Implement Strategy Result Contract

- Change:
  - Define internal strategy return contract: `DUPLICATE`, `DISTINCT`, `ABSTAIN`.
- Acceptance criteria:
  - [x] Engine rejects invalid strategy return values. [Tests: tests/dedupe/test_strategy_contract.py::test_engine_rejects_invalid_strategy_return_values]
  - [x] Contract is type-checked and unit-tested. [Tests: tests/dedupe/test_strategy_contract.py::test_contract_is_type_checked_and_unit_tested]
  - [x] Unknown statuses fail fast. [Tests: tests/dedupe/test_strategy_contract.py::test_unknown_statuses_fail_fast]
- Verification:
  - `uv run pytest -q tests/dedupe/test_strategy_contract.py`
- Execution record:
  - Date: 2026-02-18
  - Commit: `bacf067434ddad029e23ab28f62bc2f76f881a44`
  - Verification summary:
    - `uv run pytest -q tests/dedupe/test_strategy_contract.py` passed.

### C062 - Implement `exact_url` Strategy

- Change:
  - Add URL equality strategy using canonical URL hash/value.
- Acceptance criteria:
  - [x] Equivalent URLs return `DUPLICATE`. [Tests: tests/dedupe/test_strategy_exact_url.py::test_equivalent_urls_return_duplicate]
  - [x] Non-equivalent URLs return `ABSTAIN` or `DISTINCT` per design. [Tests: tests/dedupe/test_strategy_exact_url.py::test_non_equivalent_urls_return_distinct, tests/dedupe/test_strategy_exact_url.py::test_missing_url_data_returns_abstain_with_reason_code]
  - [x] Strategy logs reason code in decision record. [Tests: tests/dedupe/test_strategy_exact_url.py::test_equivalent_urls_return_duplicate, tests/dedupe/test_strategy_exact_url.py::test_non_equivalent_urls_return_distinct, tests/dedupe/test_strategy_exact_url.py::test_missing_url_data_returns_abstain_with_reason_code]
- Verification:
  - `uv run pytest -q tests/dedupe/test_strategy_exact_url.py`
- Execution record:
  - Date: 2026-02-18
  - Commit: `59ba7a0c4a573990eddd0e45734658dc02c8b161`
  - Verification summary:
    - `uv run pytest -q tests/dedupe/test_strategy_exact_url.py` passed.

### C063 - Implement `content_hash` Strategy

- Change:
  - Add exact hash strategy over normalized `title + "\n" + body`.
- Acceptance criteria:
  - [x] Equal normalized content returns `DUPLICATE`. [Tests: tests/dedupe/test_strategy_content_hash.py::test_equal_normalized_content_returns_duplicate]
  - [x] Different normalized content does not return `DUPLICATE`. [Tests: tests/dedupe/test_strategy_content_hash.py::test_different_normalized_content_does_not_return_duplicate]
  - [x] Decision metadata includes compared hash values. [Tests: tests/dedupe/test_strategy_content_hash.py::test_decision_metadata_includes_compared_hash_values]
- Verification:
  - `uv run pytest -q tests/dedupe/test_strategy_content_hash.py`
- Execution record:
  - Date: 2026-02-18
  - Commit: `669bc16de69357c7f21f53e8c068b6d6becd0d52`
  - Verification summary:
    - `uv run pytest -q tests/dedupe/test_strategy_content_hash.py` passed.

### C064 - Implement `title_similarity` Strategy

- Change:
  - Add RapidFuzz token-set ratio strategy with default threshold `0.92`.
  - Add short-title guard (`<3 tokens` => `ABSTAIN`).
- Acceptance criteria:
  - [x] Above-threshold pair returns `DUPLICATE`. [Tests: tests/dedupe/test_strategy_title_similarity.py::test_above_threshold_pair_returns_duplicate]
  - [x] Below-threshold pair returns non-duplicate decision. [Tests: tests/dedupe/test_strategy_title_similarity.py::test_below_threshold_pair_returns_non_duplicate_decision]
  - [x] Short-title cases return `ABSTAIN`. [Tests: tests/dedupe/test_strategy_title_similarity.py::test_short_title_cases_return_abstain]
- Verification:
  - `uv run pytest -q tests/dedupe/test_strategy_title_similarity.py`
- Execution record:
  - Date: 2026-02-18
  - Commit: `c1a4f5d3aa1f03415532da66738e744cd536f6fc`
  - Verification summary:
    - `uv run pytest -q tests/dedupe/test_strategy_title_similarity.py` passed.

### C065 - Implement Candidate Selection Stage

- Change:
  - Implement horizon filter + blocking keys + max candidate cap (`50`).
- Acceptance criteria:
  - [x] Candidates outside horizon are excluded. [Tests: tests/dedupe/test_candidate_selection.py::test_candidates_outside_horizon_are_excluded]
  - [x] Blocking key filters reduce candidate set deterministically. [Tests: tests/dedupe/test_candidate_selection.py::test_blocking_keys_reduce_candidate_set_deterministically]
  - [x] Candidate count never exceeds cap. [Tests: tests/dedupe/test_candidate_selection.py::test_candidate_count_never_exceeds_cap]
- Verification:
  - `uv run pytest -q tests/dedupe/test_candidate_selection.py`
- Execution record:
  - Date: 2026-02-18
  - Commit: `fa26490cf4b7e10c28f42c0bc8af905952a17048`
  - Verification summary:
    - `uv run pytest -q tests/dedupe/test_candidate_selection.py` passed.

### C066 - Implement Ordered Strategy Chain Engine

- Change:
  - Execute strategies in configured order with short-circuit semantics.
- Acceptance criteria:
  - [x] First `DUPLICATE` short-circuits evaluation. [Tests: tests/dedupe/test_chain_execution.py::test_first_duplicate_short_circuits_evaluation]
  - [x] First `DISTINCT` short-circuits evaluation. [Tests: tests/dedupe/test_chain_execution.py::test_first_distinct_short_circuits_evaluation]
  - [x] All-`ABSTAIN` path returns `DISTINCT(no_strategy_match)`. [Tests: tests/dedupe/test_chain_execution.py::test_all_abstain_returns_distinct_no_strategy_match]
- Verification:
  - `uv run pytest -q tests/dedupe/test_chain_execution.py`
- Execution record:
  - Date: 2026-02-18
  - Commit: `9df61db656c2eb52ebc2ea96ccecb86f735c403b`
  - Verification summary:
    - `uv run pytest -q tests/dedupe/test_chain_execution.py` passed.

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
