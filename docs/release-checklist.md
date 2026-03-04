# Final Release Checklist (Phase 1)

Use this checklist as a strict go/no-go gate for local release readiness.

- Release rule: if any checklist item is unchecked, outcome is `NO-GO`.
- Evidence rule: each item requires either an automated test result or a manual step result.
- Operator rule: a second engineer should be able to run this document start-to-finish with no extra context.

## Dry-Run Procedure (Second Engineer)

1. Open a terminal in the repository root (`/home/grmm/projects/tca`).
2. Sync dependencies: `uv sync --frozen`.
3. Ensure migrations apply cleanly: `uv run alembic upgrade head`.
4. Run each test command linked in checklist items below.
5. Execute required manual checks from the `Manual Validation Steps` section.
6. Record pass/fail evidence in your release notes.
7. Release decision:
   - `GO`: every item below is checked with evidence.
   - `NO-GO`: any item is unchecked or evidence is missing.

## Schema

- [ ] Schema migrations apply cleanly from empty DB. [Test: `uv run pytest -q tests/app/test_startup_migrations.py`]
- [ ] Base relational tables and foreign keys exist as expected. [Test: `uv run pytest -q tests/migrations/test_base_schema_groups.py`]
- [ ] Dedupe schema objects exist and enforce invariants. [Test: `uv run pytest -q tests/migrations/test_content_dedupe_schema.py`]

## Auth

- [ ] Bearer auth blocks unauthenticated access and allows valid tokens. [Test: `uv run pytest -q tests/api/test_bearer_auth.py`]
- [ ] Telegram auth flow covers start, code verify, and password verify transitions. [Test: `uv run pytest -q tests/api/test_telegram_auth_start.py tests/api/test_telegram_auth_verify_code.py tests/api/test_telegram_auth_verify_password.py`]

## Ingestion

- [ ] Cursor state persists and resumes correctly across polls. [Test: `uv run pytest -q tests/ingest/test_cursor_state.py`]
- [ ] Poll bounds and flood-wait handling are enforced. [Test: `uv run pytest -q tests/ingest/test_pagination_bounds.py tests/ingest/test_flood_wait.py`]

## Dedupe

- [ ] Strategy chain runs deterministically and records decisions. [Test: `uv run pytest -q tests/dedupe/test_chain_execution.py tests/dedupe/test_decision_persistence.py`]
- [ ] End-to-end flow produces stable deduplicated thread output. [Test: `uv run pytest -q tests/integration/test_smoke_pipeline.py`]

## UI

- [ ] Base shell and primary views render for authenticated flow. [Test: `uv run pytest -q tests/ui/test_shell.py tests/ui/test_thread_views.py tests/ui/test_channels_groups_views.py`]
- [ ] Setup/login UX is usable for first-run and auth transitions. [Manual: `MV-UI-01`]

## Backups

- [ ] Backup job writes a valid SQLite backup artifact. [Test: `uv run pytest -q tests/ops/test_backup_job.py`]
- [ ] Retention + backup flow preserves invariants after prune. [Test: `uv run pytest -q tests/integration/test_retention_backup.py`]

## Shutdown

- [ ] Graceful shutdown drains writer queue and preserves teardown ordering. [Test: `uv run pytest -q tests/ops/test_graceful_shutdown.py`]
- [ ] Process termination from API runtime completes without manual intervention. [Manual: `MV-SHUTDOWN-01`]

## Manual Validation Steps

### MV-UI-01

1. Run the app locally: `uv run uvicorn tca.api.app:create_app --factory --host 127.0.0.1 --port 8787`.
2. Open `http://127.0.0.1:8787/ui/`.
3. Verify the shell loads and navigation reaches setup, channels/groups, and thread views.
4. If auth is required, authenticate with local configured credentials and repeat step 3.
5. Expected result: no template/render errors, no broken navigation, and no uncaught UI exceptions in server logs.

### MV-SHUTDOWN-01

1. Start the app: `uv run uvicorn tca.api.app:create_app --factory --host 127.0.0.1 --port 8787`.
2. Trigger shutdown with `Ctrl+C`.
3. Wait up to 10 seconds for process exit.
4. Expected result: process exits cleanly, no stuck tasks, and no repeated shutdown exception loop in logs.
