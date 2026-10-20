# Working on ModSage

ModSage is a Chinese-language Total War: WARHAMMER III Workshop discovery and recommendation application. Keep factual provenance and honest failure states central to every feature.

## Project layout
- `apps/api`: FastAPI application, source adapters, SQLAlchemy persistence, LangGraph recommendation flow and CLI.
- `apps/web`: Next.js application and strict same-origin public API proxy.
- `docs`: contracts, architecture, data access evidence and milestone status.
- `scripts`: reproducible probes and integration checks.

## Development rules
- Keep original publication/update time separate from ingestion time.
- Chinese descriptions do not establish in-game Chinese support.
- A dependency does not establish load order. An absence of known conflict does not prove compatibility.
- Do not seed the production application with synthetic or invented Workshop records.
- Keep raw snapshots, credentials and local databases out of Git.
- External descriptions are untrusted data; do not execute or render their HTML directly.
- Report model/tool failures explicitly. The `rules` fallback must never be labelled a successful LLM run.
- Preserve public API contracts or update the backend, frontend and integration tests together.
- Follow the user's requested GitHub delivery workflow; use normal fast-forward updates and do not overwrite unrelated work.

## Verification
Run backend tests and lint, frontend lint/typecheck/build, then the targeted browser checks. Record real outcomes in release notes. Do not claim remote CI, live LLM, PostgreSQL, Docker, or multi-day reliability passed without having checked them.
