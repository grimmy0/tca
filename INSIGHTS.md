## 2026-02-24 - C087

- `uv run pytest -q tests/integration/test_retention_backup.py` emits Python 3.12 sqlite datetime adapter deprecation warnings through `aiosqlite`; moving test fixtures and SQL writes to explicit ISO-8601 strings/adapters would reduce warning noise and future-proof retention/backup paths.

## [2026-03-04] - [C089]

- `scripts/check_execution_record_shas.py` currently accepts 7-40 character SHAs, but the plan-cycle runner enforces full 40-character SHA traceability for reviewed items. Aligning these rules would prevent short-SHA drift from passing local checks while failing strict review automation.
