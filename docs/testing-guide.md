# Testing Guide

This guide covers the shared test harness introduced in `tests/conftest.py` and the deterministic SQLite concurrency pattern used by storage tests.

## Scope

- Local test database only (temporary files under pytest `tmp_path`).
- No network calls are required for storage-layer tests.
- Deterministic lock contention behavior for `BEGIN IMMEDIATE` flows.

## Shared Fixtures

`tests/conftest.py` provides:

- `sqlite_db_path`: temporary SQLite file path for each test.
- `sqlite_writer_pair`: two local `aiosqlite` connections configured for concurrency tests.

Connection configuration in the fixture:

- `PRAGMA journal_mode=WAL`
- `PRAGMA synchronous=NORMAL`
- `PRAGMA foreign_keys=ON`
- `PRAGMA busy_timeout=0`

`busy_timeout=0` is intentional so lock contention surfaces immediately.

## Runnable `SQLITE_BUSY` Concurrency Example

The test `tests/storage/test_begin_immediate.py` demonstrates how to assert lock behavior:

1. Connection A starts `BEGIN IMMEDIATE` and holds the write lock.
2. Connection B attempts `BEGIN IMMEDIATE` on the same file.
3. SQLite raises `SQLITE_BUSY` (typically surfaced as `database is locked`).
4. The test asserts the failure text contains `locked`.

Run:

```bash
uv run pytest -q tests/storage/test_begin_immediate.py
```

Expected result:

- Test passes with one successful assertion of deterministic write-lock contention.

## Notes for Future Storage Tests

- Reuse `sqlite_writer_pair` instead of creating ad-hoc DB setup logic.
- Keep concurrency assertions in `tests/storage/` so lock behavior remains centralized.
- Use this pattern before introducing ORM-level write queue tests.

## OpenAPI Snapshot Contract (Config/Groups)

`tests/api/test_openapi_snapshot.py` enforces a committed OpenAPI snapshot for:

- `/settings/{key}`
- `/channel-groups`
- `/channel-groups/{group_id}`
- `/channel-groups/{group_id}/channels/{channel_id}`

Snapshot artifact path:

- `tests/api/snapshots/config_groups_openapi_snapshot.json`

Run contract check:

```bash
uv run pytest -q tests/api/test_openapi_snapshot.py
```

Update flow for intentional contract changes:

1. Review and implement API contract changes.
2. Regenerate snapshot:

```bash
TCA_UPDATE_OPENAPI_SNAPSHOT=1 uv run pytest -q tests/api/test_openapi_snapshot.py
```

3. Review JSON diff in `tests/api/snapshots/config_groups_openapi_snapshot.json`.
4. Re-run the contract check command without `TCA_UPDATE_OPENAPI_SNAPSHOT`.
