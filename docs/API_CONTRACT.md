# ModSage v0.2 implementation contract

Backend: `apps/api/modsage`, FastAPI + SQLAlchemy; Next.js client: `apps/web`.
All timestamps are UTC ISO-8601. Workshop IDs are strings. Source text is untrusted.
No synthetic records in the default application. Empty/error states must remain honest.

## Public API
- GET `/api/v1/health` -> `{status, version, database, llm_configured, steam_discovery_configured}`.
- GET `/api/v1/stats` -> `{total_mods, chinese_summaries, last_synced_at, data_source, discovery_available}`. `last_synced_at` is the oldest successful fetch among stored mods, a conservative coverage boundary, not the last single-item refresh.
- GET `/api/v1/mods?q=&category=&sort=updated&page=1&page_size=12` -> `{items: Mod[], total, page, page_size}`. Sort: updated, newest, popular (only when metric available), relevant. Search includes original title, Chinese summary, tags. No fabricated popularity.
- GET `/api/v1/mods/{id}` -> Mod (with evidence[] and relations[]).
- POST `/api/v1/recommendations` body `{query: string (2–1000 characters), limit: 5 (1–10)}` -> `{run_id, status, engine, constraints, items: Recommendation[], warnings: string[]}`. Implement real LangGraph orchestration; engine `rules` if model key absent, `llm` only if actual model executed. A synchronous bounded first increment is acceptable and must be documented honestly.
- GET `/api/v1/runs/{run_id}` -> saved recommendation result, public anonymous run IDs contain no sensitive user profiles in v0.1.
- GET `/api/v1/digests/latest` -> `{date, generated_at, source_cutoff, sections: [{key,title,items:Mod[]}], warnings: string[]}`. Empty data -> valid empty response. Daily digest based on stored source timestamps; no claims of full Workshop coverage.
- POST `/api/v1/modlists/validate` body `{mod_ids:string[]}` -> `{mod_ids, missing_dependencies:[{mod_id,required_id}], known_conflicts:[], unknown_compatibility:string[], warnings:string[]}`. No automatic assumptions about load order.
- GET `/api/v1/sync/status` -> `{latest_job: RefreshJobSummary|null, last_successful_refresh_at:string|null, schedule:{enabled:boolean,timezone:"Asia/Shanghai",time:string,next_run_at:string|null}, worker:{status:"recently_seen"|"stopped"|"stale"|"unknown",last_seen_at:string|null}}`. Schedule configuration does not establish a running worker. A recent heartbeat is an observation, not a live-process guarantee. Partial/failed jobs do not advance the last full success.

### RefreshJobSummary

`{id,status,stage,source,attempts,max_attempts,created_at,started_at,completed_at,next_attempt_at,error_code,error_stage,counts:{discovered,imported,enriched}}`. Status is `queued`, `running`, `retry_wait`, `succeeded`, `partial` or `failed`; source is `api` or `public`. Error codes are safe classifications, not arbitrary provider messages. Public responses omit credentials, lease owners, fencing tokens, internal options and idempotency keys. See [daily-refresh.md](daily-refresh.md) for configuration and execution semantics.

## Mod object
`id, title, summary_zh: string|null, description_excerpt: string, author: string|null, source_url, preview_url:string|null, tags:string[], category:string, published_at:string|null, updated_at:string|null, fetched_at:string, subscribers:number|null, favorites:number|null, chinese_support: 'unknown'|'author_claimed'|'translation_available', summary_status:'pending'|'generated'|'source_chinese', compatibility_status:'unknown'|'author_claimed', dependencies_known:boolean, evidence:[{id,field,value,source_url,excerpt,observed_at}], relations:[{kind,target_id,source_url}], source_kind:'steam'`.
Do not put Workshop HTML template warnings into compatibility status. Summary is distinct from in-game language.

## Recommendation
`{mod: Mod, score:number, reasons:string[], caveats:string[]}`. Constraints include `factions`, `excluded_factions`, `excluded_categories`, `required_topics` (`artillery`, `units` for explicit requests to add content), `chinese_required`, `chinese_preferred`, and `keywords`. They must preserve negative preferences. Missing metadata cannot satisfy a mandatory requirement. No matching candidates -> empty items and warning. Ranking uses the primary title/tags and the description lead; author cross-promotion is not primary faction evidence.

## Protected ingestion / CLI
Admin HTTP endpoints, if added, require a configured non-empty `MODSAGE_ADMIN_TOKEN` and Bearer token. CLI `python -m modsage.cli init-db`, `sync --ids ...`, `discover --limit 50`, `digest`, `enrich --limit ...` operate locally. Real snapshots live in ignored local DB/data, not committed raw author content.

Refresh CLI: `enqueue-refresh [--key ASCII_KEY] [--source api|public]`, `worker [--once]`, `jobs [--limit 20]`. Equal key and options reuse the existing job; conflicting options on an existing key fail. The worker uses the same database as the API. With automatic refresh disabled, `worker --once` handles only previously enqueued work and exits normally on an empty queue. Recommendation requests remain synchronous and bounded; the refresh queue does not change their contract.

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
