# TCA Design Document: Local Telegram Aggregator (Option A)

## Document Status

- Version: 3
- Last updated: 2026-02-16
- Scope: Local deployment only, Telegram only
- Audience: Engineering, product, operations

## 1. Overview

TCA is a local-first application that runs on the user's own machine and produces one unified, deduplicated thread from Telegram channels the user follows.

This design intentionally optimizes for:

- simple installation (`docker compose up -d`),
- privacy (no managed backend),
- deterministic deduplication behavior,
- operational reliability on a single host.

No multi-provider support is planned. Telegram is the only supported platform.

## 2. Scope and Non-Goals

## 2.1 In Scope

- Authenticate as a Telegram user account (not bot-only flow).
- Poll configured Telegram channels.
- Normalize messages into a canonical item model.
- Deduplicate messages using configurable strategy chain and time horizon.
- Expose a local API and minimal local web UI for setup and usage.
- Run as a single container with persistent local storage.

## 2.2 Out of Scope

- Managed/cloud service.
- Providers other than Telegram.
- Real-time webhook ingestion from third parties.
- Guaranteed seamless migration to a distributed architecture.

## 3. Requirements

## 3.1 Functional Requirements

- User can connect their Telegram account with user credentials/session.
- User can add/remove Telegram channels from ingestion.
- User can set dedupe horizon globally and per channel group.
- User can choose dedupe strategy chain order.
- User can view one merged thread with duplicate counts and source attribution.
- User can inspect why items were marked as duplicates.
- User can trigger manual re-poll/re-dedupe jobs.

## 3.2 Non-Functional Requirements

- Local-first privacy by default.
- Safe restart behavior with durable state.
- Bounded data growth via retention policy.
- API access control enabled by default.
- Works on consumer hardware.

## 4. Concrete Technology Choices

These are fixed for Phase 1.

- Python runtime: `3.12.x` only (`>=3.12,<3.13`) for dependency stability. UUIDv7 was added in Python 3.14, so Phase 1 uses UUIDv4 for cluster keys. [S20]
- Web framework: FastAPI.
- ASGI server: Uvicorn.
- Telegram client: `telethon==1.42.*` pinned (stable branch). [S21]
- DB access: SQLAlchemy 2.x + aiosqlite.
- Migration tool: Alembic (`render_as_batch=True` for SQLite). [S9]
- String similarity: RapidFuzz.
- Hashing primitives: Python stdlib `hashlib.sha256`. [S18]
- UI stack: server-rendered Jinja2 + HTMX + Pico CSS (no Node build pipeline).
- Packaging/runtime: Docker + Docker Compose.

## 5. Runtime Architecture (Single Container)

## 5.1 Process Topology

Single `tca` process contains:

- REST API,
- minimal embedded web UI,
- scheduler,
- ingest workers,
- dedupe engine,
- retention/maintenance jobs.

Persistent volume:

- `/data/tca.db` (SQLite),
- `/data/backups/` (SQLite backups),
- `/data/logs/` (optional logs).

## 5.2 Module Boundaries

- `api/`: HTTP routes and auth middleware.
- `ui/`: Jinja2 templates + HTMX handlers.
- `auth/`: Telegram login flow and secret encryption.
- `ingest/`: channel polling and message fetch.
- `normalize/`: canonical item transformation.
- `dedupe/`: strategy chain and cluster operations.
- `storage/`: repositories and transaction boundaries.
- `scheduler/`: interval/jitter/backoff orchestration.
- `ops/`: backups, retention, and health checks.

## 5.3 Lifecycle Integration (FastAPI + Telethon)

- Use FastAPI lifespan to initialize and teardown shared resources. [S1]
- Create one shared Telethon client per account in app state.
- Connect Telethon clients on startup and disconnect on shutdown.
- Never create per-request Telethon clients.
- Keep one asyncio event loop for both API and Telethon client usage. [S13]

## 6. Persistence, Concurrency, and Reliability

## 6.1 SQLite Configuration (Mandatory)

At startup, always enforce:

- `PRAGMA journal_mode=WAL;`
- `PRAGMA synchronous=NORMAL;`
- `PRAGMA foreign_keys=ON;`
- `PRAGMA busy_timeout=5000;`

Write transaction mode:

- Use `BEGIN IMMEDIATE` for writer transactions to acquire write lock early.
- Implement via SQLAlchemy event handlers attached to `AsyncEngine.sync_engine`. [S7] [S8] [S24]

## 6.2 Write Concurrency Model

- All writes go through a single in-process async write queue.
- Read queries use separate read-only sessions.
- API writes and ingest writes are serialized by the writer queue.

## 6.3 Lock Timeout Behavior

If a write hits `SQLITE_BUSY`:

1. Retry with exponential backoff (`50ms`, `100ms`, `200ms`, `400ms`, `800ms`).
2. Maximum 5 retries.
3. If still failing:
- ingest job is requeued with delay,
- API write returns `503` with retry hint.

## 6.4 ORM Async Constraints

- Lazy loading is not allowed in async ORM access paths.
- All relationship access must use eager loading (`selectinload`/`joinedload`) or explicit SQL.
- MissingGreenlet is treated as a programming error in review/testing gates. [S6]

## 6.5 Migration Execution Model

- Run `alembic upgrade head` synchronously before API starts accepting requests.
- Never run migrations concurrently with ingest/poll workers.
- Alembic env must set `render_as_batch=True` for SQLite compatibility. [S9]

## 6.6 Idempotency

- Unique constraints prevent duplicate message inserts.
- Dedupe membership table has unique `(cluster_id, item_id)`.
- Crash-safe reconciliation job processes items with `dedupe_state='pending'`.

## 7. Telegram Authentication and Secret Management

## 7.1 Auth Model

Telegram has no OAuth equivalent for this use case. TCA uses Telegram user auth via Telethon:

- User provides `api_id` and `api_hash` from Telegram API development tools (`my.telegram.org`). [S2] [S3]
- User completes phone + OTP (and 2FA password if enabled).
- TCA stores encrypted session material locally.

## 7.2 Session Storage Type

- Use Telethon `StringSession` persisted in `telegram_accounts`.
- Do not use Telethon default `SQLiteSession` files.
- Reason: avoid second session database and keep backup/restore simpler. [S4]

## 7.3 Encryption Model

- Data encryption key (DEK) encrypts sessions/tokens at rest.
- DEK is wrapped by key-encryption key (KEK).
- KEK derivation uses Argon2id from user passphrase.
- Argon2id baseline: memory 64 MiB, iterations 3, parallelism 1, salt 16 bytes.
- Argon2id is used for passphrase/KDF, not for API bearer token verification. [S16] [S17]

## 7.4 Startup Modes

- `secure-interactive` (default): user unlocks with passphrase after restart.
- `auto-unlock` (optional, lower security): KEK from mounted local secret file.

This resolves the unattended restart tradeoff explicitly.

## 7.5 Key Rotation

Rotation is two-phase and crash-recoverable:

1. Generate new KEK and write rotation metadata state.
2. Rewrap row keys in batches with per-row version markers.
3. Commit completion marker only when all rows are updated.
4. On crash, resume from last completed row/version.

## 7.6 Registration Failure Handling

If API ID registration/login cannot proceed:

- store actionable notification (`auth_registration_blocked`),
- pause auth retries with exponential backoff,
- show UI guidance with retry and support links.

This aligns with Telegram and Telethon guidance that registration can be blocked for some numbers and should be retried later. [S2] [S3]

## 8. Ingestion Design (Telegram Only)

## 8.1 Cursor Contract

Cursor is a typed JSON object persisted per channel:

```json
{
  "channel_id": 123456789,
  "last_message_id": 45210,
  "next_offset_id": null,
  "last_polled_at": "2026-02-15T18:30:00Z"
}
```

## 8.2 Polling

- Default poll interval: 5 minutes.
- Jitter: +/- 20%.
- Manual poll endpoint available.

## 8.3 Pagination

Per poll execution is bounded:

- `max_pages_per_poll` (default `5`),
- `max_messages_per_poll` (default `500`).

If more data remains, `next_offset_id` is stored and the next scheduler run continues from that point.

## 8.4 Dynamic Rate Limit Handling

- Telethon/Telegram `FLOOD_WAIT` is treated as provider backpressure.
- Channel is paused until `now + wait_seconds`.
- Scheduler avoids paused channels and records event.
- Use Telethon `flood_sleep_threshold=0` to keep sleeps explicit in scheduler control. [S5] [S11]

## 8.5 Account Restriction Handling

If repeated flood waits or auth errors indicate account risk:

- pause all polling for that account,
- emit high-severity notification,
- require explicit user re-enable in UI.

## 8.6 Error Recording

All ingest/parse/auth errors are written to `ingest_errors` with:

- `channel_id`,
- `stage` (`fetch|normalize|dedupe|auth`),
- `error_code`,
- `error_message`,
- `payload_ref`,
- `created_at`.

## 9. Data Model

Core tables:

- `telegram_accounts`
- `telegram_channels`
- `channel_groups`
- `channel_group_members`
- `channel_state` (cursor, pause-until, last-success)
- `raw_messages`
- `items`
- `dedupe_clusters`
- `dedupe_members`
- `dedupe_decisions`
- `ingest_errors`
- `notifications`
- `settings`

Key indexes (Phase 1 mandatory):

- `raw_messages(channel_id, message_id)` unique
- `items(channel_id, message_id)` unique
- `items(raw_message_id)` unique (where non-null)
- `items(published_at)`
- `items(canonical_url_hash)`
- `items(content_hash)`
- `channel_group_members(channel_id)` unique
- `channel_group_members(group_id, channel_id)` index
- `dedupe_members(item_id)`
- `dedupe_clusters(representative_item_id)`
- `ingest_errors(created_at)`

## 9.1 Channel Group Model

- `channel_groups`: `id`, `name`, `description`, `dedupe_horizon_minutes_override` (nullable), `created_at`, `updated_at`.
- `channel_group_members`: `group_id`, `channel_id`, `created_at`.
- Phase 1 rule: one channel can belong to at most one group (`channel_group_members.channel_id` unique) so dedupe override precedence is deterministic.
- Horizon resolution order:
1. group override (`channel_groups.dedupe_horizon_minutes_override`) if set,
2. global dedupe horizon from `settings`.

## 9.2 raw_messages -> items Relationship

- `items.raw_message_id` is a nullable FK to `raw_messages.id` with `ON DELETE SET NULL`.
- Phase 1 cardinality is current-state 1:1: one current `raw_messages` row maps to one `items` row (`items.raw_message_id` unique when present).
- `items` identity is logical message key (`channel_id`, `message_id`) so message edits upsert the same `items` row.
- `raw_messages` stores the latest raw payload for that logical message in Phase 1 (no full edit-history table yet).

## 9.3 Settings Schema

`settings` table schema (Phase 1):

- `id`
- `key` (for example `dedupe.default_horizon_minutes`)
- `value_json`
- `updated_at`

Constraints:

- unique (`key`)
- per-group dedupe horizon is stored on `channel_groups.dedupe_horizon_minutes_override`, not in `settings`

## 9.4 FTS5 Implementation Constraints

- Use SQLite FTS5 external-content table for title/body index.
- Create and maintain INSERT/UPDATE/DELETE triggers from `items` to FTS table.
- Implement FTS creation and trigger SQL in Alembic migrations.
- Use repository-level raw SQL for `MATCH` queries and joins back to `items`.
- Run `rebuild` command during migration/repair when index inconsistency is detected. [S10]

## 10. Deduplication Design

## 10.1 Strategy Chain Semantics (Precise)

Each strategy returns one of:

- `DUPLICATE(score, reason)`
- `DISTINCT(reason)`
- `ABSTAIN(reason)`

Execution per candidate pair is ordered and deterministic:

1. First `DUPLICATE` short-circuits as duplicate.
2. First `DISTINCT` short-circuits as non-duplicate.
3. `ABSTAIN` moves to next strategy.
4. If all strategies abstain, result is `DISTINCT` with reason `no_strategy_match`.

## 10.2 Candidate Reduction (Avoid O(n^2))

For each new item, candidates are selected in stages:

1. Time window filter (`horizon`).
2. Blocking keys:
- same `canonical_url_hash`, or
- same URL domain, or
- shared rare title tokens (FTS5 query).
3. Cap candidate list (`max_candidates`, default `50`).
4. Run strategy chain only on reduced set.

## 10.3 Strategy Definitions

- `exact_url`: equal normalized canonical URL.
- `content_hash`: equal SHA-256 hash over normalized `title + "\n" + body`.
- `title_similarity`: RapidFuzz `token_set_ratio / 100.0`, default threshold `0.92`.
- `llm_verify` (optional): disabled by default.

Normalization pipelines (hash and similarity intentionally diverge):

Hash normalization pipeline:

- Unicode NFKC.
- lowercase.
- strip tracking query params (`utm_*`, `fbclid`, `gclid`) and Telegram link wrappers.
- replace non-alphanumeric characters with single spaces.
- collapse whitespace and trim.

Similarity normalization pipeline:

- Unicode NFKC.
- lowercase.
- strip tracking query params (`utm_*`, `fbclid`, `gclid`) and Telegram link wrappers.
- preserve word boundaries (do not remove whitespace separators).
- collapse repeated whitespace only.

Guardrails for `title_similarity`:

- If either title has fewer than 3 tokens after normalization, return `ABSTAIN`.
- For CJK scripts with no whitespace tokenization, fall back to character-level `ratio` threshold in separate strategy `title_cjk_ratio` (optional).

RapidFuzz behavior motivates this guardrail because token_set_ratio can return high scores when one title is a subset of the other. [S19]

## 10.4 Cluster Merge Rules

If new item matches multiple clusters in one run:

1. Select target cluster = smallest `cluster_id`.
2. Move members from other clusters to target within one transaction.
3. Write `cluster_merge` event to `dedupe_decisions`.
4. Recompute representative item deterministically.

## 10.5 Cluster Key

- `cluster_key` is UUIDv4.
- Purpose: stable external identifier for APIs/UI.
- It is not derived from content and has no scoring semantics.

## 11. Representative Selection and Thread Query

## 11.1 Representative Item Selection

Representative is recalculated on cluster change using deterministic priority:

1. Item with canonical URL present.
2. Higher text completeness (`len(title)+len(body)`).
3. Earliest `published_at`.
4. Lowest `item_id` tiebreaker.

## 11.2 Thread Strategy (Phase 1 Decision)

Phase 1 uses on-demand thread query only.

- No materialized `thread_entries` table in Phase 1.
- Query uses indexed joins over `dedupe_clusters`, `dedupe_members`, `items`, and `telegram_channels`.
- This keeps write-path simpler and avoids stale materialization invalidation logic.

## 12. API, Auth, and UI

## 12.1 API Authentication

- `GET /health` is public on localhost.
- All other endpoints require bearer token.
- Bootstrap token generated with `secrets.token_urlsafe(32)`.
- Store only SHA-256 digest of token in DB.
- Verify with constant-time compare against digest.

Rationale: high-entropy bearer tokens should be random and unguessable; Argon2id remains for passphrase/KDF use. [S25] [S16] [S18]

## 12.2 Bind and CORS

- Default bind: `127.0.0.1` only.
- LAN exposure requires explicit config.
- CORS default deny; allowlist required for external UI origins.

## 12.3 Minimal UI (Phase 1)

Phase 1 includes a minimal web UI to avoid API-only usability:

- first-run setup (auth + key unlock),
- channel management,
- thread view,
- dedupe decision drill-down,
- alert panel (degraded channels, auth needed).

First-run auth wizard is explicit multi-step flow:

1. enter `api_id`/`api_hash`,
2. submit phone number,
3. submit OTP code,
4. submit 2FA password when required,
5. confirm session saved.

No stdin-driven Telethon interactive login is used in container runtime.

## 12.4 Channel Group Management API

- `GET /channel-groups`
- `POST /channel-groups`
- `PATCH /channel-groups/{group_id}`
- `DELETE /channel-groups/{group_id}`
- `PUT /channel-groups/{group_id}/channels/{channel_id}`
- `DELETE /channel-groups/{group_id}/channels/{channel_id}`

## 12.5 Configuration Surface (Static vs Dynamic)

Environment variables (static, restart required):

- `TCA_DB_PATH`
- `TCA_BIND`
- `TCA_MODE`
- `TCA_LOG_LEVEL`
- `TCA_SECRET_FILE`

`settings` rows (dynamic, UI/API editable, no restart required):

- `dedupe.default_horizon_minutes`
- `dedupe.threshold.title_similarity`
- `scheduler.default_poll_interval_seconds`
- `scheduler.max_pages_per_poll`
- `scheduler.max_messages_per_poll`
- `retention.raw_messages_days`
- `retention.items_days`
- `retention.ingest_errors_days`
- `retention.dedupe_decisions_days`
- `backup.retain_count`
- per-group dedupe horizon is edited via `PATCH /channel-groups/{group_id}` (`dedupe_horizon_minutes_override`)

Hardcoded defaults:

- Used only on first boot when no setting row exists.
- Defaults are written into `settings` at initialization and become user-editable afterwards.

## 13. Deletion Semantics

`DELETE /channels/{id}` supports two modes:

- default `soft-delete`:
- channel disabled and hidden,
- historical items remain,
- clusters remain stable.

- `purge=true` hard delete:
- remove channel messages/items,
- recompute affected clusters and representatives,
- record audit event.

## 14. Retention, Backups, and Maintenance

## 14.1 Retention

Configurable defaults:

- `raw_messages_retention_days=30`
- `ingest_errors_retention_days=90`
- `dedupe_decisions_retention_days=180`
- `items_retention_days=365` (or `0` for keep forever)

## 14.2 Prune Procedure (Ordered and Batched)

Daily prune job execution order:

1. delete expired `raw_messages` in batches (`LIMIT 500`); referenced `items.raw_message_id` is nulled by FK action,
2. delete expired `items` in batches (`LIMIT 500`) to bound lock time,
3. recompute affected cluster representatives,
4. delete empty clusters,
5. delete orphaned `dedupe_members` and `dedupe_decisions`,
6. delete expired `ingest_errors`.

FTS5 trigger work is part of the batch design, so large deletes do not hold write lock for long windows.

## 14.3 Backup and Restore

- Nightly SQLite backup to `/data/backups/tca-YYYYMMDD.db`.
- Backups use SQLite Online Backup API (`sqlite3.Connection.backup()`), not direct file copy.
- Run `PRAGMA integrity_check` against backup file after write.
- Retain last `N` backups (default `14`).
- Restore procedure: stop container, replace DB with selected backup, run integrity check, restart.

This follows SQLite and Python guidance for live-database backups. [S11] [S12]

## 14.4 Graceful Shutdown

On SIGTERM:

1. stop scheduler intake,
2. wait up to 8 seconds for in-flight tasks,
3. flush writer queue,
4. close Telethon client connections,
5. close DB/session cleanly,
6. exit before Docker SIGKILL timeout.

## 15. Notifications and User Feedback

User-visible issues are surfaced via:

- `notifications` table,
- UI alerts,
- optional webhook for local automation.

Alert types:

- auth re-login needed,
- flood-wait pause,
- repeated ingest failures,
- backup failure,
- account-risk pause.

## 16. Telegram Terms and Risk Model

TCA explicitly acknowledges Telegram API monitoring and abuse controls.

- User account API access can trigger throttling or restrictions when behavior looks abusive.
- App should always prefer conservative polling and respect flood-wait values.
- User must supply credentials and operate in compliance with Telegram terms.

Risk controls:

- account-level pause on repeated `FLOOD_WAIT`/auth failures,
- explicit high-severity alert requiring user action,
- recovery flow documentation in UI.

Decision on Bot API hybrid:

- Not part of Phase 1.
- Reason: product goal is "aggregate channels the user follows" and this is anchored on user account access with MTProto; Bot API is bot-account scoped and does not replace user-account subscription access. [S3] [S23]

## 17. Docker Installation

```yaml
services:
  tca:
    image: ghcr.io/<owner>/tca:0.1.0
    container_name: tca
    ports:
      - "8787:8787"
    environment:
      - TCA_DB_PATH=/data/tca.db
      - TCA_BIND=127.0.0.1
      - TCA_MODE=secure-interactive
    volumes:
      - tca-data:/data
    restart: unless-stopped

volumes:
  tca-data:
```

Versioning policy:

- Docs use pinned semver tags, not `latest`.
- Production deployments should optionally pin digest (`image:tag@sha256:...`) for reproducibility. [S14] [S15]

## 18. Phase Plan

## Phase 1 (MVP)

- Telegram auth + channel ingestion.
- Configurable dedupe horizon and strategies (`exact_url`, `content_hash`, `title_similarity`).
- Local API with bearer auth.
- Minimal local web UI.
- Retention + backup + alerts.

## Phase 2 (hardening)

- Optional `llm_verify` strategy.
- Better ranking and filtering.
- Performance tuning for larger histories.

No non-Telegram provider work is planned.

## 19. Architect Review Resolution Matrix (R1-R15)

All architecture review items are addressed and reflected in this document.

- **R1. SQLite concurrency**
- Status: Addressed
- Update: Mandatory WAL, single writer queue, explicit lock retry behavior, and BEGIN IMMEDIATE policy in Section 6.

- **R2. Adapter interface gaps**
- Status: Addressed
- Update: Telegram-specific typed cursor, flood-wait handling, bounded pagination, and explicit ingest error table in Sections 8 and 9.

- **R3. Dedupe chain ambiguity**
- Status: Addressed
- Update: Deterministic strategy semantics, candidate reduction, cluster merge rules, and hash/similarity definitions in Section 10.

- **R4. OAuth feasibility**
- Status: Addressed
- Update: OAuth framing removed; Telegram user auth/session flow only in Section 7.

- **R5. Key management contradictions**
- Status: Addressed
- Update: dual startup modes and crash-safe key rotation in Section 7.

- **R6. API has no auth**
- Status: Addressed
- Update: bearer token auth for non-health endpoints and localhost default bind in Section 12.

- **R7. Retention and indexes**
- Status: Addressed
- Update: retention windows, prune procedure, and index requirements in Sections 9 and 14.

- **R8. Missing dependency choices**
- Status: Addressed
- Update: concrete framework/ORM/migration/library selections in Section 4.

- **R9. Materialized vs on-demand thread**
- Status: Addressed
- Update: Phase 1 fixed to on-demand only in Section 11.

- **R10. No UI in Phase 1**
- Status: Addressed
- Update: minimal web UI is mandatory in Section 12 and Phase 1 scope in Section 18.

- **R11. Representative selection undefined**
- Status: Addressed
- Update: deterministic representative rules in Section 11.

- **R12. Source deletion cascade unspecified**
- Status: Addressed
- Update: defined `soft-delete` and `purge=true` semantics in Section 13.

- **R13. Optimistic Option B migration claim**
- Status: Addressed
- Update: migration guarantees removed from scope in Sections 2 and 18.

- **R14. Python 3.14 risk**
- Status: Addressed
- Update: runtime pinned to Python 3.12.x in Section 4.

- **R15. Missing concerns (shutdown, notifications, idempotency, backup, CORS, ToS)**
- Status: Addressed
- Update: addressed in Sections 6, 12, 14, 15, and 16.

## 20. Technology-Specific Resolution Matrix (T1-T14)

All technology-specific review items are addressed and reflected in this document.

- **T1. Argon2id for bearer tokens**
- Status: Addressed
- Update: bearer tokens now use random generation + SHA-256 digest storage; Argon2id reserved for passphrase/KDF in Sections 7 and 12. [S25] [S16] [S18]

- **T2. BLAKE3 dependency overhead**
- Status: Addressed
- Update: replaced BLAKE3 with stdlib SHA-256; removed Rust-native hash dependency from design in Sections 4 and 10. [S18] [S22]

- **T3. Python version contradiction**
- Status: Addressed
- Update: design now explicitly targets Python 3.12.x and uses UUIDv4 in Phase 1 to avoid uuid7 runtime mismatch in Sections 4 and 10. [S20]

- **T4. Telethon + FastAPI lifecycle integration**
- Status: Addressed
- Update: explicit lifespan-managed Telethon connect/disconnect and web-based multi-step login flow in Sections 5, 7, and 12. [S1] [S2] [S4] [S13]

- **T5. Telethon account risk handling**
- Status: Addressed
- Update: added conservative rate handling, account-risk pause policy, and user-visible recovery flow in Sections 8, 15, and 16. [S3] [S5]

- **T6. SQLAlchemy async eager loading requirement**
- Status: Addressed
- Update: explicit no-lazy-loading rule and eager-loading requirement in Section 6. [S6]

- **T7. BEGIN IMMEDIATE for writes**
- Status: Addressed
- Update: writer transaction mode changed to BEGIN IMMEDIATE with SQLAlchemy event integration in Section 6. [S7] [S8] [S24]

- **T8. Alembic batch mode for SQLite**
- Status: Addressed
- Update: `render_as_batch=True` and startup migration ordering now mandatory in Sections 4 and 6. [S9]

- **T9. FTS5 trigger and raw SQL complexity**
- Status: Addressed
- Update: explicit external-content FTS table, trigger setup, and repository-level SQL boundaries in Section 9. [S10]

- **T10. token_set_ratio behavior on short titles**
- Status: Addressed
- Update: short-title abstain rule (<3 tokens) and optional CJK fallback strategy in Section 10. [S19]

- **T11. Minimal UI technology not specified**
- Status: Addressed
- Update: Phase 1 UI stack fixed to Jinja2 + HTMX + Pico CSS and auth wizard flow defined in Sections 4 and 12.

- **T12. Prune job and cluster/FTS side effects**
- Status: Addressed
- Update: ordered, batched prune procedure with cluster recomputation and orphan cleanup in Section 14.

- **T13. Backup method specificity**
- Status: Addressed
- Update: backups now require SQLite Online Backup API plus post-backup integrity check in Section 14. [S11] [S12]

- **T14. Docker `latest` tag usage**
- Status: Addressed
- Update: installation example now uses pinned semver and optional digest pinning in Section 17. [S14] [S15]

## 21. Research References

- [S1] FastAPI lifespan events: https://fastapi.tiangolo.com/advanced/events/
- [S2] Telethon signing in (API ID/hash, login flow): https://docs.telethon.dev/en/v2/basic/signing-in.html
- [S3] Telegram obtaining API ID and abuse monitoring: https://core.telegram.org/api/obtaining_api_id
- [S4] Telethon sessions and StringSession: https://docs.telethon.dev/en/stable/concepts/sessions.html
- [S5] Telethon RPC/FLOOD_WAIT behavior: https://docs.telethon.dev/en/stable/concepts/errors.html
- [S6] SQLAlchemy async and MissingGreenlet guidance: https://docs.sqlalchemy.org/20/errors.html and https://docs.sqlalchemy.org/20/orm/extensions/asyncio.html
- [S7] SQLite transactions (DEFERRED/IMMEDIATE): https://www.sqlite.org/lang_transaction.html
- [S8] SQLAlchemy SQLite begin event control: https://docs.sqlalchemy.org/en/14/dialects/sqlite.html
- [S9] Alembic batch mode for SQLite: https://alembic.sqlalchemy.org/en/latest/batch.html
- [S10] SQLite FTS5 external content and trigger sync: https://www.sqlite.org/fts5.html
- [S11] Python sqlite3 backup API: https://docs.python.org/3.12/library/sqlite3.html
- [S12] SQLite Online Backup API details: https://www.sqlite.org/backup.html
- [S13] Telethon Client connect/disconnect and async context use: https://docs.telethon.dev/en/v2/modules/client.html
- [S14] Docker Compose image reference supports digest pinning: https://docs.docker.com/reference/compose-file/services/
- [S15] Docker best practice on mutable tags and pinning versions/digests: https://docs.docker.com/build/building/best-practices/
- [S16] OWASP password storage and Argon2id recommendations: https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html
- [S17] OWASP session entropy guidance: https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html
- [S18] Python hashlib module (SHA-256): https://docs.python.org/3/library/hashlib.html
- [S19] RapidFuzz token_set_ratio behavior and examples: https://rapidfuzz.github.io/RapidFuzz/Usage/fuzz.html
- [S20] Python uuid docs (uuid7 added in 3.14): https://docs.python.org/3.14/library/uuid.html
- [S21] Telethon stable docs version (1.42.0): https://docs.telethon.dev/
- [S22] blake3 PyPI packaging notes (Rust toolchain if no wheel): https://pypi.org/project/blake3/
- [S23] Telethon Bot API vs MTProto: https://docs.telethon.dev/en/v2/concepts/botapi-vs-mtproto.html
- [S24] SQLAlchemy asyncio events via `sync_engine`: https://docs.sqlalchemy.org/20/orm/extensions/asyncio.html
- [S25] Python secrets module (`token_urlsafe`, entropy guidance): https://docs.python.org/3/library/secrets.html

## 22. Final Clarification Matrix (Q1-Q4)

- **Q1. \"Channel groups\" has no design support**
- Status: Addressed
- Update: Added `channel_groups` and `channel_group_members` tables, override precedence, indexes, and channel-group management API in Sections 9 and 12.

- **Q2. `raw_messages` -> `items` relationship undefined**
- Status: Addressed
- Update: Defined `items.raw_message_id` nullable FK with `ON DELETE SET NULL`, current-state 1:1 mapping, logical key upsert semantics, and prune behavior in Sections 9 and 14.

- **Q3. \"Keep alphanumeric content\" ambiguous**
- Status: Addressed
- Update: Split normalization into separate hash and similarity pipelines so similarity preserves token boundaries while hash uses stronger character stripping in Section 10.

- **Q4. Configuration surface undefined**
- Status: Addressed
- Update: Added explicit `settings` schema plus static env var vs dynamic settings vs hardcoded-default contract in Sections 9 and 12.
