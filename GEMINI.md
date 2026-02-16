# GEMINI.md - TCA (Threaded Channel Aggregator)

Instructional context for Gemini CLI when working in the TCA repository.

## Project Overview

TCA is a local-first Python application that aggregates updates from Telegram channels, deduplicates repeated stories across sources, and presents them in a single, unified chronological thread. It is designed to run as a single Docker container with local SQLite storage, prioritizing privacy and simplicity.

- **Primary Goal:** Keep a clean, deduplicated feed from multiple Telegram sources.
- **Project Stage:** Early scaffold (MVP phase).
- **Core Architecture:** Pipeline-based (Ingest → Normalize → Dedupe → Timeline → API/UI).

## Tech Stack

- **Runtime:** Python 3.12.x (pinned for stability)
- **Package Manager:** `uv`
- **Web Framework:** FastAPI + Uvicorn
- **Telegram Client:** Telethon (User-account MTProto access, not Bot API)
- **Database:** SQLite (WAL mode) with SQLAlchemy 2.x (Async) & Alembic
- **Deduplication:** RapidFuzz (similarity), SHA-256 (hashing)
- **Frontend:** Jinja2 + HTMX + Pico CSS (Server-rendered, no Node.js build pipeline)
- **Containerization:** Docker + Docker Compose

## Development Commands

```bash
# Install and sync dependencies
uv sync

# Run the application (currently a placeholder)
uv run python main.py

# Planned testing (not yet implemented)
uv run pytest
```

## Project Structure (Planned)

The project follows a modular monolith structure:

- `api/`: FastAPI routes and authentication middleware.
- `ui/`: Jinja2 templates and HTMX handlers for the web interface.
- `auth/`: Telegram login flow, session management, and secret encryption (Argon2id).
- `ingest/`: Telegram channel polling and message fetching logic.
- `normalize/`: Transformation of raw provider messages into a canonical schema.
- `dedupe/`: Multi-stage strategy chain for identifying duplicate content.
- `storage/`: SQLite repository layer using SQLAlchemy's async extension.
- `scheduler/`: Orchestration of polling intervals, jitter, and backoff.
- `docs/`: Detailed design and architecture specifications.

## Key Development Conventions

### 1. SQLite Concurrency & Reliability
- **WAL Mode:** Mandatory `PRAGMA journal_mode=WAL;`.
- **Write Consistency:** Use `BEGIN IMMEDIATE` for all write transactions to avoid deadlocks.
- **Single Writer:** All writes must go through a single in-process async write queue.
- **Migrations:** Managed via Alembic with `render_as_batch=True` for SQLite compatibility.

### 2. Async Implementation
- **No Lazy Loading:** Eager loading (`selectinload`/`joinedload`) is required for all ORM relationship access to prevent `MissingGreenlet` errors.
- **Shared Event Loop:** Telethon and FastAPI must share the same asyncio event loop.

### 3. Deduplication Strategy
- **Strategy Chain:** Ordered execution: Exact URL → Content Hash → Title Similarity.
- **Short-circuiting:** The first strategy to return `DUPLICATE` or `DISTINCT` stops the chain; otherwise, it `ABSTAIN`s to the next strategy.
- **Normalization:** Separate pipelines for hashing (strict character stripping) and similarity (token-boundary preserving).

### 4. Security
- **Auth:** All non-health API endpoints require a bearer token.
- **Encryption:** Telegram session material is encrypted at rest using a Data Encryption Key (DEK) wrapped by a Master Key (KEK) derived from a user passphrase.

## Data Model Highlights
- `raw_messages`: Original payloads for audit and re-processing.
- `items`: Normalized, deduplicated entries.
- `dedupe_clusters`: Groups of identical/similar items.
- `telegram_accounts`: Encrypted session data for MTProto access.
