## 2026-02-24 - C087

- `uv run pytest -q tests/integration/test_retention_backup.py` emits Python 3.12 sqlite datetime adapter deprecation warnings through `aiosqlite`; moving test fixtures and SQL writes to explicit ISO-8601 strings/adapters would reduce warning noise and future-proof retention/backup paths.
