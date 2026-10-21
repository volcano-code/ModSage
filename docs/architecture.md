# Architecture and delivery boundaries

## v0.4

One FastAPI codebase owns catalog, source snapshots, structured facts, recommendation runs, refresh jobs and digest editions. Next.js renders the Chinese UI and proxies only an explicit public API allowlist. A CLI performs controlled ingestion and enrichment; a separate worker executes durable catalog refreshes. SQLite supports a minimal local setup; PostgreSQL is the deployment target in Compose.

The recommendation graph remains bounded. The UI now submits durable asynchronous jobs; the legacy synchronous endpoint stays compatible. `engine=rules` means deterministic query interpretation; `engine=llm` means successful model interpretation. The separate `retrieval.method` records the actual BM25 or hybrid RRF path. An LLM and an embedding model are independently configured capabilities; neither configuration alone establishes execution. Missing credentials do not lead to fabricated model results.

Raw metadata snapshots and generated Chinese descriptions are different records. A SHA-256 hash identifies normalized content; fetching unchanged content must not duplicate a revision. Metric snapshots remain separate from content updates. API responses preserve `unknown` values for unsupported claims.

## Retrieval and source freshness

The default retrieval channel is field-weighted BM25 over source titles (weight 3), tags (2), and bounded author introductions (1), with English normalization, Chinese bigrams and domain aliases. Hard constraints filter the bounded source catalog before either retrieval channel. Catalog search opts into Chinese summary text to preserve its search contract; recommendations and embedding input stay source-only. Database and ranking work in the async recommendation path runs outside the event loop through worker threads.

Optional hybrid retrieval computes exact cosine similarity over valid cached vectors and fuses lexical/semantic ranks with equal-weight reciprocal-rank fusion (`k=60`). This is a bounded JSON-vector implementation compatible with SQLite and PostgreSQL; it is not an ANN index, pgvector deployment or learned reranker. Each channel retains a configurable maximum number of candidates. The default catalog scan is 5,000 source records ordered by ID and warns when truncated. The catalog listing/search endpoint still scans its full stored catalog; larger-scale SQL/search-engine indexing remains future work.

`mod_embeddings` uses `(mod_id, namespace)` as its key. The namespace identifies endpoint, model, requested dimensions and document schema; an individual fingerprint identifies source hash and exact indexed text. Manual backfill skips fresh records, performs network calls outside database transactions, and rechecks/locks the source before committing a derived vector. PostgreSQL locks the source row; SQLite manual backfills must run serially. An unconfigured provider, missing index, stale source, invalid dimensions or provider failure triggers an explicit BM25 fallback when no usable semantic path remains. Partially indexed catalogs can combine current vectors with lexical-only candidates and report that limitation.

Recommendations re-read source records after network waits and verify fingerprints and hard constraints again before producing the persisted result. This prevents serving an old vector as current evidence after a concurrent source update. Provider calls are bounded in time, input batch, dimensions and response bytes; invalid vectors never enter the index. Source text remains untrusted data, not instructions. See [retrieval operations](retrieval.md) and [frozen evaluation](retrieval-evaluation.md) for exact scope and known failures.

## Daily refresh execution

`refresh_jobs` stores unique idempotency keys, bounded options, attempts, stage checkpoints and safe outcomes. `refresh_leases` provides one catalog-writing lease for competing refresh workers; every worker commit checks both the catalog lease and the job's fencing token. Expired owners cannot renew or finalize after another worker takes ownership. `refresh_workers` records recent heartbeats separately from the configured schedule.

The scheduler uses `Asia/Shanghai` dates and catches up only the current due day. Automatic enqueue is disabled by default. A worker discovers bounded updated/newest/popular candidates, imports validated details, optionally generates a bounded number of summaries, and refreshes the stored digest. API discovery requires a key; public-page discovery is an explicit option. Missing data and source failures never erase the existing catalog. Partial runs do not advance the last fully successful refresh timestamp.

Provider calls occur outside database transactions. Stage results and their database changes commit together. Before import has committed, a retry repeats bounded discovery to restore the source adapter's in-memory metadata; after import it resumes later stages. Model attempts are recorded before a request so recovering the same job does not repeatedly charge the same item. A crash can therefore leave an attempted item without a generated summary, reported as incomplete. This is not a claim of exactly-once external network execution.

The lease serializes these refresh workers, not every possible manual CLI or external database writer. Avoid launching manual ingestion against the same catalog while a refresh is active. This release adds new tables through the existing initialization path without altering existing columns; schema migrations remain a future requirement. Operational commands and configuration are in [daily-refresh.md](daily-refresh.md).

## Durable recommendation execution

`recommendation_jobs` stores input, token hash, token-scoped idempotency key, total attempt budget, lease and result. `recommendation_events` stores monotonically numbered snapshots; state and events commit atomically. A short database admission lock coordinates active queue capacity. Workers claim through atomic conditional updates, with PostgreSQL row locking and `SKIP LOCKED`; every stage, heartbeat and completion checks the claim token and lease expiry. Cancellation invalidates the claim so an old worker cannot publish a late result.

Provider calls occur outside database transactions. A recovered attempt reruns the bounded graph, so a crash may repeat an external call; this is not a LangGraph checkpointer or exactly-once external execution. Normal failures need explicit retry within the task's original attempt budget. Graceful shutdown releases recoverable work; a killed process is handled through lease expiry.

The public proxy forwards only explicit job routes and capability/replay headers. Protected SSE reads persisted events and supports a bounded connection with cursor replay; it does not buffer a full response or expose tokens in URLs. The browser keeps a bounded local history and restores the selected job. New durable results never enter the legacy public run store. Local API startup includes an embedded worker; Compose runs the same worker loop as a separate service. Operational details and limits are in [recommendation-tasks.md](recommendation-tasks.md).

## Planned next increments

1. Expand independently judged multilingual retrieval data, measure a real embedding provider against the frozen baseline, and select scalable indexing/reranking only after quality and cost measurements.
2. Add account-scoped history, task retention/deletion and transactionally persisted Outbox notifications; introduce node checkpointing if measured workflows need it.
3. Add authenticated, server-side personal lists and preferences. Browser-local favorites in the initial UI are not cross-device account storage.
4. Add evidence-backed relation extraction and editor review; avoid equating dependency closure with a proven load order.
5. Add per-user notifications with deduplication and accumulate real multi-day operational history for the implemented scheduler.

These items remain roadmap requirements until their implementation and acceptance evidence are available. No automatic installation, universal compatibility guarantee or all-Workshop coverage is claimed.

## Evidence and sources

- Steam app ID: `1142710`.
- [GetPublishedFileDetails / GetCollectionDetails](https://partner.steamgames.com/doc/webapi/ISteamRemoteStorage).
- [QueryFiles](https://partner.steamgames.com/doc/webapi/IPublishedFileService): use the project's own user API key and validate returned fields.
- [Steam API terms](https://steamcommunity.com/dev/apiterms).
- [LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence).

API-based discovery and a bounded public-page fallback must be identified separately. An upstream failure is not evidence that there are zero mods. Data freshness reports the last successful observation, never the last failed attempt.

## Deployment model

The Compose defaults bind the database, API and frontend to loopback for local evaluation. Public hosting requires a suitable HTTPS reverse proxy, production secrets and a database backup policy. CI and a local browser check validate specific configurations; neither substitutes for multi-day production observation.
