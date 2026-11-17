# ModSage v1.0 — Baseline Audit and Architecture Decisions

Audit date: 2026-11-17  
Audited branch: `main`  
Audited commit: `39811e27a711716af43379adf56c686618671806`  
Audit branch: `research/v1.0-baseline-20261117`

## 1. Executive summary

ModSage v0.6 is a substantial full-stack foundation. It already has a Steam Workshop ingestion adapter, a FastAPI catalog, Next.js UI, bounded LangGraph recommendation workflow, BM25 retrieval, optional exact-vector/RRF retrieval, durable recommendation jobs, a durable catalog-refresh worker, provider call quotas, Alembic baseline migration, and backup/restore checks.

The v1.0 program should extend this system rather than replace it. The main gaps are not missing framework choices; they are live-data validation, independently reviewed retrieval quality, evidence-rich relation modeling, tool-driven Agent behavior, account-scoped data, notification delivery, and multi-instance operational readiness.

## 2. Repository and CI baseline

- Default branch: `main`.
- Latest commit observed: `39811e27a711716af43379adf56c686618671806`.
- A separate branch was created for this audit: `research/v1.0-baseline-20261117`.
- Before the v1.0 task issues were added, the repository had no open issues or pull requests.
- GitHub Actions run [40336420957](https://github.com/volcano-code/ModSage/actions/runs/40336420957), associated with the latest commit, completed successfully on 2026-10-26.
- All four jobs passed: backend tests/PostgreSQL, frontend lint/typecheck/build, browser integration, and Docker Compose startup/integration.
- The run published backend, browser, and deployment artifacts.
- The repository's v0.6 verification report records 467 backend tests, 91% Python statement coverage, 15 Playwright tests, successful production build, SQLite restore verification, and PostgreSQL dump/restore verification.

Important distinction: the test counts and coverage above are recorded in the repository's verification report and CI artifacts. This audit inspected the remote repository and run metadata; it did not execute the test suite on a developer workstation. CI's provider contract tests use loopback fixtures and do not validate real LLM, embedding, or authenticated Steam credentials.

## 3. Current implementation map

### Data ingestion
- `apps/api/modsage/sources/steam.py` implements Workshop detail reads, collection reads, authenticated `IPublishedFileService/QueryFiles`, bounded cursor pagination, and limited public HTML discovery.
- `apps/api/modsage/ingestion.py` imports validated records, snapshots source payloads, hashes content, tracks daily metrics, and writes known `requires` relations.
- `scripts/steam_probe.py` is a bounded live probe and writes IDs plus field-coverage observations under ignored `data/`.
- `docs/data-access-report.md` documents a 2026-10-20 public-discovery probe: 50 candidate IDs, 49 accepted Mods, and complete dependency fields observed for 0/49 records. Authenticated QueryFiles and multi-day continuity were not verified in that report.

### Retrieval and recommendation
- `apps/api/modsage/retrieval.py` implements bilingual tokenization, field-weighted BM25, exact cosine ranking over stored JSON vectors, and RRF.
- `apps/api/modsage/embeddings.py` implements provider calls, source/document fingerprints, namespace-aware vector storage, status, and manual bounded backfill.
- `apps/api/modsage/recommendations.py` builds a bounded three-node LangGraph: `understand`, `retrieve_and_filter`, and `verify`.
- Hard constraints are enforced in deterministic code; LLM interpretation is optional and cannot weaken selected hard exclusions.
- The current vector implementation is exact scan over JSON vectors. It is not pgvector, ANN, or a learned reranker.

### Durable execution and provider controls
- Recommendation jobs persist protected task state, ordered events, lease/fencing metadata, bounded retries, cancellation, and replayable SSE progress.
- Catalog refresh uses persistent jobs, a catalog lease, checkpoints, retries, and worker heartbeat.
- Provider calls are gated by database-backed shared call-count limits and recorded as bounded operational metadata.
- Recovery reruns bounded graph work after interrupted attempts; it does not promise exactly-once external provider calls or constitute a full LangGraph checkpointer.

### Relations and user experience
- `ModRelation` currently stores a source Mod, relation kind, target ID, source URL, and observation time.
- Ingestion only replaces known `requires` relations when source dependency metadata is considered complete.
- Compatibility and in-game language default to `unknown`; current UI explicitly warns that prerequisite checks are not runtime compatibility tests.
- Browser saved Mods are stored in localStorage (bounded to 100); the current schema has no User/Account model or account-scoped collections/preferences/feedback API.

### Database and deployment
- Alembic currently has the immutable `0001_v05_baseline` revision.
- Guarded migration startup, schema validation, SQLite backup/restore, and PostgreSQL restore smoke tests are present.
- Docker Compose runs PostgreSQL, API, recommendation worker, frontend, and an optional catalog refresh worker.
- API request rate limiting is process-local in `main.py`; it must not be treated as a distributed limit when multiple API replicas are deployed.

## 4. Verified facts vs open validation

| Area | Verified from repository / CI | Still requires validation |
|---|---|---|
| Steam | Adapter and bounded probe exist; historical public probe report exists | Real authenticated QueryFiles, broader catalog coverage, multi-day refresh continuity |
| Retrieval | BM25, optional vector channel, RRF and frozen v1 evaluator exist | Independent v2 labels, real embedding quality, held-out gains, latency/cost |
| Relations | Basic relation table and dependency checks exist | Evidence lifecycle, relation review, version-aware conflict/compatibility graph |
| Agent | Bounded LangGraph workflow and durable job wrapper exist | Typed tool registry, planner, multi-step tools, verifier benchmark, checkpoint semantics |
| Users | Browser-local saved list and local history exist | Authentication, account-scoped storage, preferences, feedback and deletion |
| Notifications | Global digest and refresh exist | Outbox, per-user subscriptions, delivery, opt-out and retries |
| Operations | CI, Compose and backup/restore tests exist | Real staged deployment, multi-replica limits, monitoring, retention and continuity |

## 5. Architecture decisions for v1.0

1. Preserve v0.6 as the stable baseline; do not rewrite the current app.
2. Keep deterministic hard-constraint filtering outside model authority.
3. Keep BM25 as the production default until real, independently evaluated evidence justifies a change.
4. Preserve retrieval v1 data and publish a separate v2 evaluation dataset with provenance, adjudicated labels, and a held-out test split.
5. Treat relation extraction as candidate generation. Only source-authored or human-reviewed evidence may be presented as confirmed.
6. Do not infer load order from dependency closure. Represent unknown and stale states explicitly.
7. Extend the current LangGraph workflow with typed tools and bounded planning only where multi-step tasks need it. Do not add multi-agent orchestration without an ablation showing value.
8. Add account-scoped data through a maintained OIDC integration; do not build a bespoke password system.
9. Add notifications through a transactional outbox and idempotent workers.
10. Use additive Alembic migrations for every v1.0 schema change; test upgrade from a copy of v0.6 and restore to a new target before rollout.
11. Keep provider calls opt-in, budgeted, and recorded. CI must never use real paid credentials.
12. Do not claim public production availability, complete Workshop coverage, or proven game compatibility until those exact properties have been tested.

## 6. Ordered delivery plan

| Phase | Work | Exit gate |
|---|---|---|
| 0 | Baseline audit and architecture decisions | Audit is recorded; v0.6 remains unchanged |
| 1 | Authenticated Steam probe, catalog quality and incremental sync | Redacted live report; idempotent import; bounded multi-day observation |
| 2 | Retrieval evaluation v2 | Independent adjudication and immutable held-out split |
| 3 | Real embedding / hybrid / reranker benchmark | Reproducible quality-latency-cost comparison; BM25 fallback retained |
| 4 | Evidence-backed relation graph | Versioned migration; provenance and review states; cycle-safe bounded traversal |
| 5 | Tool-driven Agent v2 | Typed tools, hard-constraint preservation, evidence verification and task benchmark |
| 6 | Account-scoped collections and preferences | OIDC, ownership checks, IDOR tests, export/delete |
| 7 | Notification outbox | Atomic enqueue, idempotent delivery, retries, unsubscribe |
| 8 | Product integration and release | Full CI, security review, staged deployment, backup/restore and demos |

## 7. Phase 0 exit checklist

- [x] Identify latest main commit and repository state.
- [x] Inspect CI run and individual job outcomes.
- [x] Inspect ingestion, retrieval, Agent, relation, user collection, migration, and deployment implementations.
- [x] Separate historical/live evidence from fixture-based CI evidence.
- [x] Create a dedicated audit branch.
- [x] Create v1.0 roadmap issues.
- [ ] Review and accept this baseline document.
- [ ] Begin Phase 1 only after local Steam key and local deployment access are available for explicit live verification.

## 8. Safety boundaries

- No production database was changed during this audit.
- No provider key or secret was requested, stored, or committed.
- No live Steam, LLM, or embedding call was made during this audit.
- No existing source data, migration history, or user collection was modified.
