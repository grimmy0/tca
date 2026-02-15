# TCA (Threaded Channel Aggregator)

TCA is a channel aggregator that collects updates from all channels you follow and merges them into one unified thread.

## Problem

If you follow many channels, the same story appears multiple times across sources. TCA aims to keep only one canonical item per story so your feed stays clean.

## Goals

- Pull updates from multiple channel types (RSS, blogs, newsletters, video channels, and social feeds).
- Normalize all incoming items into a shared schema.
- Deduplicate repeated stories across sources.
- Build a single chronological thread for easy scanning.
- Keep source attribution so you can open the original channel post.

## How Deduplication Will Work

TCA is designed to combine several strategies:

- URL canonicalization to collapse tracking/query variants.
- Exact-content hashing for obvious duplicates.
- Near-duplicate matching using title and body similarity.
- Time-window checks so repeated reposts can still be grouped.

## Initial Architecture

- `ingest/`: source adapters per channel/provider.
- `normalize/`: convert source-specific payloads into one model.
- `dedupe/`: duplicate detection and canonical item selection.
- `timeline/`: ranking + merged thread generation.
- `storage/`: persistence for seen items and dedupe clusters.

## Development

```bash
uv sync
uv run python main.py
```

## Roadmap

- [ ] Define core item schema and storage model.
- [ ] Implement first source adapter (RSS).
- [ ] Build URL and exact-match dedupe.
- [ ] Add similarity-based near-duplicate detection.
- [ ] Generate a merged "single thread" view.
