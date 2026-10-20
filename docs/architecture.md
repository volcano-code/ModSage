# Architecture and delivery boundaries

## v0.1

One FastAPI codebase owns catalog, source snapshots, structured facts, recommendation runs and digest editions. Next.js renders the Chinese UI and proxies only an explicit public API allowlist. A CLI performs controlled ingestion and enrichment. SQLite supports a minimal local setup; PostgreSQL is the deployment target in Compose.

The first recommendation increment is a bounded synchronous LangGraph workflow with persisted input, constraints and output. `engine=rules` means deterministic query interpretation and lexical ranking; it does **not** mean an LLM, embedding model, reranker or external agent has executed. If configured, the provider can help interpret requirements and generate evidence-bound Chinese summaries. Missing credentials do not lead to fabricated model results.

Raw metadata snapshots and generated Chinese descriptions are different records. A SHA-256 hash identifies normalized content; fetching unchanged content must not duplicate a revision. Metric snapshots remain separate from content updates. API responses preserve `unknown` values for unsupported claims.

## Planned next increments

1. Expand the evaluated multilingual retrieval dataset and add embeddings/pgvector with baseline comparisons.
2. Move long-running runs to durable workers, add numbered SSE events, cancel/retry and transactionally persisted Outbox events.
3. Add authenticated, server-side personal lists and preferences. Browser-local favorites in the initial UI are not cross-device account storage.
4. Add evidence-backed relation extraction and editor review; avoid equating dependency closure with a proven load order.
5. Add scheduled discovery and per-user notifications with deduplication and real operational history.

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
