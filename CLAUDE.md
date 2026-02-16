# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

TCA (Threaded Channel Aggregator) is a local-first Python application that aggregates updates from multiple channel types (RSS, YouTube, Reddit, etc.), deduplicates repeated stories, and presents a single merged thread. It runs as a single container with SQLite storage (Option A: Local Monolith architecture).

The project is in early scaffold stage. The detailed architecture spec lives in `docs/option-a-local-design.md`.

## Development Commands

```bash
# Install/sync dependencies
uv sync

# Run the application
uv run python main.py
```

No test runner, linter, or formatter is configured yet. When these are added, they will likely use `pytest` for testing and be invoked via `uv run`.

## Architecture

The planned module structure follows a pipeline pattern:

**ingest** (source adapters + polling) → **normalize** (canonical schema) → **dedupe** (strategy chain) → **timeline** (merged thread) → **api** (REST endpoints)

Supporting modules: `auth/` (OAuth/token management), `storage/` (SQLite repository layer), `scheduler/` (per-source polling with backoff), `observability/` (logs, metrics, health).

### Key Patterns

- **Source adapters** are pluggable, each implementing: `validate_config`, `fetch_since`, `normalize`, `rate_limit_hint`
- **Deduplication** uses an ordered strategy chain (exact_url → content_hash → title_similarity); each strategy can accept, reject, or pass. Decisions are recorded for explainability.
- **Data flow**: raw items are stored in `raw_items` (audit trail), then normalized into `items`, grouped into `dedupe_clusters`, and surfaced via `thread_entries`

### Environment Variables

- `TCA_MASTER_KEY` — encryption key for stored tokens
- `TCA_DB_PATH` — SQLite database path (default `/data/tca.db`)
- `TCA_LOG_LEVEL` — log verbosity

## Tech Stack

- Python ≥3.14, managed with `uv`
- SQLite for persistence
- Docker Compose for deployment (port 8787)
