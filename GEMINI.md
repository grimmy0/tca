# GEMINI.md - TCA

Instructional context for Gemini CLI when working in this repository.

## Project Overview

TCA is a local-first Telegram channel aggregator that:

- authenticates with Telegram user account credentials,
- ingests channel updates,
- deduplicates repeated stories,
- serves a unified thread view.

Scope is Telegram-only.

## Source-of-Truth Docs

1. `docs/option-a-local-design.md`
2. `docs/implementation-plan.md`
3. `docs/testing-guide.md`

## Current Implementation Status

The codebase is in early execution of the plan. Implemented so far includes:

- app startup migrations, settings seeding, and unlock/bootstrap auth initialization,
- API endpoints for health, settings, channel groups, and Telegram auth start/verify,
- storage/auth primitives: WAL PRAGMAs, `BEGIN IMMEDIATE`, writer queue, encrypted sessions, auth session state, key rotation metadata,
- strict pre-commit lint/type gate and shared SQLite concurrency test harness.

## Tech Stack

- Python 3.12.x
- `uv` package management
- FastAPI + Uvicorn
- Telethon
- SQLAlchemy + aiosqlite + Alembic
- RapidFuzz + SHA-256
- Jinja2 + HTMX + Pico CSS

## Development Commands

```bash
# install deps (runtime + dev)
uv sync --extra dev

# run app placeholder
uv run python main.py

# strict lint/type gate
scripts/lint_strict.sh

# run tests
uv run pytest -q

# pre-commit hook management
uv run pre-commit install
uv run pre-commit run --all-files
```

## Quality Rules

- Treat warnings as failures in lint/type checks.
- Keep changes aligned with commit-atomic plan items.
- Do not introduce scope beyond Telegram.
