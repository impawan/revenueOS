# Cut list

## Left out, and why
- Real HubSpot, Slack and Gong integrations: synthetic data was allowed; the webhook is HubSpot-shaped.
- Auth and roles, multi-region views, a second seam, root-cause tagging, evaluation harness, vector retrieval.
- Persistence of pending agent runs: the app resets and re-seeds on boot.
- Tests for Tasks 7-12 (agent, LLM drafter, watcher, API, UI): dropped by user decision mid-build. Verified only by inline stub and TestClient runs.
- Verification never done: real Anthropic API call, browser view of the UI, manual checklist, Archify visual check (failed in sandbox).
- Per-task reviews for Tasks 6-12 were skipped; the final whole-branch review covered them (findings and fixes in `docs/build-log.md`).

## Deferred from the final review
- One global lock held across Claude calls (a slow call blocks decisions on every brief, and boot seeding drafts sequentially).
- Business days are computed in UTC, not US local time.
- Watcher tick and `ingest_won` duplicate check are read-then-append, not atomic.
- A breach trigger keeps pointing at the draft current at breach time, even if it is later rejected.
- Seed mix differs from spec section 9 (all three colours are still present).
- Webhook-created briefs are stamped with real time, not the demo clock.

## What breaks first at 10x volume
1. `os_events` full-table scans on every state build and watcher tick: add indexes and incremental projections or a materialised view.
2. In-memory BM25 over a growing library: move to pgvector or Qdrant with hybrid search.
3. SQLite single writer under concurrent webhooks: move to Postgres.
4. Single-process watcher and in-memory LangGraph checkpoints: durable queue and Postgres checkpointer.
5. 2-second UI polling: SSE or websockets.
6. LLM latency in the request path: make drafting fully async with status events.
