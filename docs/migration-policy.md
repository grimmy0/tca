# Migration Policy (SQLite + Alembic)

## Why Batch Mode Is Mandatory

TCA runs on SQLite for local-first deployment. SQLite has limited `ALTER TABLE` support compared to server databases, so table shape changes often require table-rebuild semantics instead of direct in-place DDL updates.

To keep migrations predictable, Alembic must run with:

- `render_as_batch=True` in both offline and online migration contexts.

This project treats missing batch mode as a migration policy violation.

## Migration Checklist

1. Pre-checks:
   - confirm target revision graph and downgrade path,
   - verify migration script uses batch operations for table-shape changes.
2. Lock considerations:
   - keep DDL scope minimal,
   - avoid long-running data backfills in the same migration step.
3. Rollback expectations:
   - ensure downgrade path is explicit and reversible where feasible,
   - document non-reversible steps in migration PR notes before merge.
