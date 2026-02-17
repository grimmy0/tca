# CLAUDE.md

This file provides guidance to Claude Code when working with this repository.

## Project Overview

TCA is a local-first Telegram channel aggregator. It ingests channel updates using a user account, deduplicates repeated stories, and exposes a unified thread.

- Scope: Telegram only.
- Architecture source-of-truth: `docs/option-a-local-design.md`.
- Execution source-of-truth: `docs/implementation-plan.md`.

## Current State

- Early implementation phase with storage/auth/api scaffolding, scheduler core loop, and ingest helpers in place.
- Startup runs migrations, seeds dynamic settings, and initializes unlock mode + bootstrap bearer token.
- API endpoints: `/health`, `/settings/{key}`, `/channels`, `/channel-groups`, `/jobs/poll-now/{channel_id}`, `/notifications`, `/notifications/{notification_id}/ack`, `/auth/telegram/start`, `/auth/telegram/verify-code`, `/auth/telegram/verify-password`, `/openapi.json` (bearer auth required except `/health`).
- Storage/auth primitives: WAL PRAGMAs, `BEGIN IMMEDIATE`, writer queue, encrypted session storage, auth session state, key rotation metadata.

## Development Commands

```bash
# install runtime + dev dependencies
uv sync --extra dev

# run placeholder app entrypoint
uv run python main.py

# strict lint/type gate (same as pre-commit)
scripts/lint_strict.sh

# run all tests
uv run pytest -q
```

## Quality Gate

Pre-commit is configured and should remain installed:

```bash
uv run pre-commit install
uv run pre-commit run --all-files
```

The hook runs strict checks and treats warnings as failures.

## Tech Stack

- Python 3.12.x
- FastAPI + Uvicorn
- Telethon (MTProto user account)
- SQLAlchemy + aiosqlite + Alembic
- RapidFuzz + SHA-256 content hashing
- Jinja2 + HTMX + Pico CSS

## Key Constraints

- SQLite WAL and `BEGIN IMMEDIATE` are mandatory in runtime design.
- Async ORM usage must avoid lazy-loading patterns.
- No non-Telegram provider scope in this project.
