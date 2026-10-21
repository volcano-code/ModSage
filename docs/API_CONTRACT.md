# ModSage v0.4 implementation contract

Backend: `apps/api/modsage`, FastAPI + SQLAlchemy; Next.js client: `apps/web`.
All timestamps are UTC ISO-8601. Workshop IDs are strings. Source text is untrusted.
No synthetic records in the default application. Empty/error states must remain honest.

## Public API
- GET `/api/v1/health` -> `{status, version, database, llm_configured, steam_discovery_configured, embedding_configured, retrieval_mode:'bm25'|'hybrid'}`. Configuration flags do not establish provider connectivity or actual execution.
- GET `/api/v1/stats` -> `{total_mods, chinese_summaries, last_synced_at, data_source, discovery_available}`. `last_synced_at` is the oldest successful fetch among stored mods, a conservative coverage boundary, not the last single-item refresh.
- GET `/api/v1/mods?q=&category=&sort=updated&page=1&page_size=12` -> `{items: Mod[], total, page, page_size}`. Sort: updated, newest, popular (only when metric available), relevant. Search includes original title, Chinese summary, tags. No fabricated popularity.
- GET `/api/v1/mods/{id}` -> Mod (with evidence[] and relations[]).
- POST `/api/v1/recommendations` body `{query: string (2–1000 characters), limit: 5 (1–10)}` -> `{run_id, status, engine, constraints, items: Recommendation[], warnings: string[], retrieval?:RetrievalRun}`. Real LangGraph orchestration; engine `rules` means deterministic requirement interpretation, `llm` means successful model interpretation. Retrieval is tracked separately. The request remains synchronous and bounded. Failed and older saved runs may omit retrieval metadata.
- GET `/api/v1/runs/{run_id}` -> saved recommendation result, public anonymous run IDs contain no sensitive user profiles in v0.1.
- POST `/api/v1/recommendation-jobs` body identical to recommendations, headers `X-Recommendation-Token` and `Idempotency-Key` -> HTTP 202 `RecommendationJob`. Durable async route used by the UI. Token-scoped equal keys and bodies return the same job; conflicting bodies return 409; full queue returns 429.
- GET `/api/v1/recommendation-jobs/{uuid}` -> current `RecommendationJob`, requiring the capability header.
- GET `/api/v1/recommendation-jobs/{uuid}/events` -> bounded `text/event-stream`; `progress` events contain committed job snapshots, monotonically increasing per-job IDs; replay uses the larger of `Last-Event-ID` and `after`. Invalid cursor -> 422, future cursor -> 409. Heartbeats are comments. The connection closes after the current terminal state is delivered or its duration limit; disconnect does not cancel the job.
- POST `/api/v1/recommendation-jobs/{uuid}/cancel` and `/retry` -> current/newly queued snapshot; capability required. Cancellation invalidates the execution lease. Retry accepts failed tasks within the original total attempt budget; active repeated retries do not create another attempt.
- GET `/api/v1/digests/latest` -> `{date, generated_at, source_cutoff, sections: [{key,title,items:Mod[]}], warnings: string[]}`. Empty data -> valid empty response. Daily digest based on stored source timestamps; no claims of full Workshop coverage.
- POST `/api/v1/modlists/validate` body `{mod_ids:string[]}` -> `{mod_ids, missing_dependencies:[{mod_id,required_id}], known_conflicts:[], unknown_compatibility:string[], warnings:string[]}`. No automatic assumptions about load order.
- GET `/api/v1/sync/status` -> `{latest_job: RefreshJobSummary|null, last_successful_refresh_at:string|null, schedule:{enabled:boolean,timezone:"Asia/Shanghai",time:string,next_run_at:string|null}, worker:{status:"recently_seen"|"stopped"|"stale"|"unknown",last_seen_at:string|null}}`. Schedule configuration does not establish a running worker. A recent heartbeat is an observation, not a live-process guarantee. Partial/failed jobs do not advance the last full success.

### RefreshJobSummary

`{id,status,stage,source,attempts,max_attempts,created_at,started_at,completed_at,next_attempt_at,error_code,error_stage,counts:{discovered,imported,enriched}}`. Status is `queued`, `running`, `retry_wait`, `succeeded`, `partial` or `failed`; source is `api` or `public`. Error codes are safe classifications, not arbitrary provider messages. Public responses omit credentials, lease owners, fencing tokens, internal options and idempotency keys. See [daily-refresh.md](daily-refresh.md) for configuration and execution semantics.

### RecommendationJob

`{id,query,limit,status:'queued'|'running'|'completed'|'failed'|'cancelled',stage:'queued'|'understand'|'retrieve'|'verify'|'completed'|'failed'|'cancelled',attempts,max_attempts,created_at,started_at,completed_at,last_event_id,error_code:string|null,result:RecommendationRunResult|null}`. Capability and lease tokens, hashes and idempotency keys are never exposed. Credential syntax is 32–128 URL-safe characters. Missing/malformed capability -> 401; wrong capability or unknown task -> 404. Durable results stay in protected jobs, not in the legacy public `/runs` store, even though `result.run_id` equals the job ID.

Job validation failures use a generic 422 without echoing the query or credentials. Pre-stream storage failures return a sanitized 503 with `Retry-After: 2`; failures after SSE has begun close the transport for reconnect. Queue capacity returns 429 / `Retry-After: 10`, stream capacity 429 / `Retry-After: 2`, and the process-local request guard 429 / `Retry-After: 60`. A 429 during an ambiguous submission replay does not prove that no task was previously created: clients must retain the original capability/key to recover it. Safe execution error codes are `RequestBudgetExceeded`, `WorkerError`, `LeaseExpired`, and `WorkerInterrupted`.

Every state change and corresponding historical event snapshot share a database transaction. Claimed execution is fenced against cancellation and lease recovery. Process interruption can rerun the bounded graph and repeat an external provider call; this is task-level recovery, not exactly-once external execution or LangGraph node checkpointing. See [recommendation-tasks.md](recommendation-tasks.md).

## Mod object
`id, title, summary_zh: string|null, description_excerpt: string, author: string|null, source_url, preview_url:string|null, tags:string[], category:string, published_at:string|null, updated_at:string|null, fetched_at:string, subscribers:number|null, favorites:number|null, chinese_support: 'unknown'|'author_claimed'|'translation_available', summary_status:'pending'|'generated'|'source_chinese', compatibility_status:'unknown'|'author_claimed', dependencies_known:boolean, evidence:[{id,field,value,source_url,excerpt,observed_at}], relations:[{kind,target_id,source_url}], source_kind:'steam'`.
Do not put Workshop HTML template warnings into compatibility status. Summary is distinct from in-game language.

## Recommendation
`{mod: Mod, score:number, reasons:string[], caveats:string[], retrieval?:RetrievalEvidence}`. Constraints include `factions`, `excluded_factions`, `excluded_categories`, `required_topics` (`artillery`, `units` for explicit requests to add content), `chinese_required`, `chinese_preferred`, and `keywords`. They must preserve negative preferences. Missing metadata cannot satisfy a mandatory requirement. No matching candidates -> empty items and warning. Recommendation ranking uses the primary title/tags and the description lead; author cross-promotion is not primary faction evidence. Catalog search additionally includes the displayed Chinese summary, preserving its existing search contract.

`RetrievalRun = {method:'bm25'|'hybrid_rrf', requested_method:'bm25'|'hybrid_rrf', candidate_count:number, eligible_count:number, indexed_count:number, stale_count:number}`. Counts refer to the bounded recommendation scan, not global Workshop or complete index coverage. `candidate_count` is the retrieved candidate union before final limit/current-source verification; `eligible_count` is the scanned set satisfying hard constraints. Index counts apply to eligible documents in the configured namespace. In BM25 mode the index is not inspected and both index counts are zero. A requested hybrid run can correctly report `method:'bm25'` and an explanatory warning.

`RetrievalEvidence = {lexical_score:number, semantic_score:number|null, matched_terms:string[], channels:('lexical'|'semantic')[]}`. BM25, cosine and RRF scores have different scales; none is a probability, compatibility confidence or a score comparable across queries. A source can appear in one or both channels. UI must tolerate missing metadata in historical runs and show actual execution rather than configured capability.

## Protected ingestion / CLI
Admin HTTP endpoints, if added, require a configured non-empty `MODSAGE_ADMIN_TOKEN` and Bearer token. CLI `python -m modsage.cli init-db`, `sync --ids ...`, `discover --limit 50`, `digest`, `enrich --limit ...` operate locally. Real snapshots live in ignored local DB/data, not committed raw author content.

Refresh CLI: `enqueue-refresh [--key ASCII_KEY] [--source api|public]`, `worker [--once]`, `jobs [--limit 20]`. Equal key and options reuse the existing job; conflicting options on an existing key fail. The worker uses the same database as the API. With automatic refresh disabled, `worker --once` handles only previously enqueued work and exits normally on an empty queue. This refresh queue is distinct from the recommendation queue.

Recommendation CLI: `recommendation-worker [--once]` executes persistent jobs with lease recovery. The API starts an embedded worker by default for local development; Compose disables that mode and starts a dedicated service. Existing synchronous recommendation endpoints remain compatible.

Retrieval CLI: `embedding-status` reports configured model, catalog total, bounded scan, current/stale/missing records; `embed --limit 20 --batch-size 8` explicitly requests bounded provider calls (limit 1–200, batch 1–16). Valid current records are skipped. Run manual backfills serially per database; this CLI is not a durable distributed queue. Source/provider/schema changes are handled by fingerprint and namespace validation. No automatic embedding calls occur with the default BM25 recommendation mode.

## Source module contract (adapter owner)
Provide `modsage.sources.steam.SteamClient`, `SteamSourceError`, `SteamConfigurationError`, and `SteamItem` dataclass.
`SteamClient(api_key: str|None=None, client: httpx.AsyncClient|None=None)` async methods:
- `get_details(ids:list[str]) -> list[SteamItem]`
- `query_files(sort:str='updated', limit:int=50) -> list[str]` requires real configured key; fails clearly without key.
- `discover_public(limit:int=50, sort:str='updated') -> list[str]` OPTIONAL public Workshop HTML limited discovery, no login or access-control bypass; record source mode explicitly.
- `get_collection_ids(collection_id:str) -> list[str]`
- `aclose()` only closes owned HTTP client.
SteamItem fields: `id,title,description,author,source_url,preview_url,tags,published_at,updated_at,fetched_at,subscribers,favorites,required_ids,raw,dependencies_known`. `dependencies_known` defaults to false; an empty unknown list must not erase earlier explicit source relationships. Times as timezone-aware datetime or None. `raw` only for internal snapshots. Reject different consumer_app_id, non-Mod type, private/unavailable records; distinguish response failure from empty data. Escape/untrusted sanitization shared helper `clean_description(text)`.

## Configuration
Use MODSAGE_ prefix: DATABASE_URL (SQLite quickstart, PostgreSQL Docker), STEAM_API_KEY (optional), LLM_API_KEY, LLM_BASE_URL, LLM_MODEL, ADMIN_TOKEN, CORS_ORIGINS. Frontend `API_BASE_URL` is server-side backend URL; use Next.js same-origin `/api/...` proxy to avoid exposing credentials or browser localhost mismatch. No automatic demo seeding.

Retrieval adds RETRIEVAL_MODE, RETRIEVAL_CATALOG_LIMIT, RETRIEVAL_CANDIDATE_LIMIT, EMBEDDING_API_KEY, EMBEDDING_BASE_URL, EMBEDDING_MODEL, EMBEDDING_DIMENSIONS, EMBEDDING_TIMEOUT_SECONDS and EMBEDDING_MIN_SIMILARITY. Blank optional dimensions mean unspecified. Defaults, budgets and fallback semantics are documented in [retrieval.md](retrieval.md). `RETRIEVAL_CATALOG_LIMIT` bounds recommendation/index scans, not the existing full-scan catalog search endpoint.
