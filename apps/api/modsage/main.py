import asyncio
import hmac
import logging
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager, suppress
from typing import Annotated, Literal

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, select, text

from modsage import __version__
from modsage.catalog import iso, list_mods, serialize_mod
from modsage.config import Settings, get_settings
from modsage.database import init_database, make_database
from modsage.digests import build_digest, validate_modlist
from modsage.jobs import sync_status
from modsage.models import Mod, RecommendationRun
from modsage.provider_usage import public_provider_status
from modsage.recommendation_api import register_recommendation_api
from modsage.recommendation_jobs import run_recommendation_worker
from modsage.recommendations import recommend


class RecommendationInput(BaseModel):
    query: str = Field(min_length=2, max_length=1000)
    limit: int = Field(default=5, ge=1, le=10)


class ModlistInput(BaseModel):
    mod_ids: list[Annotated[str, Field(pattern=r"^[1-9][0-9]{0,19}$")]] = Field(
        min_length=1, max_length=100
    )

    @field_validator("mod_ids")
    @classmethod
    def validate_ids(cls, values):
        if any(int(value) > 2**64 - 1 for value in values):
            raise ValueError("Workshop IDs must fit uint64")
        return list(dict.fromkeys(values))


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine, session_factory = make_database(settings.database_url)

    @asynccontextmanager
    async def lifespan(app):
        init_database(engine)
        worker = None
        if settings.recommendation_worker_enabled:
            worker = asyncio.create_task(run_recommendation_worker(session_factory, settings))

            def report_stop(task):
                if not task.cancelled() and task.exception() is not None:
                    logging.getLogger(__name__).error("Recommendation worker stopped unexpectedly")

            worker.add_done_callback(report_stop)
        try:
            yield
        finally:
            if worker is not None:
                worker.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await worker
            engine.dispose()

    app = FastAPI(
        title="ModSage API",
        version=__version__,
        lifespan=lifespan,
        description="真实来源目录、有界推荐与持久化后台任务；支持有序进度事件和断线恢复。",
    )
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.engine = engine
    app.state.recommendation_slots = asyncio.Semaphore(4)
    app.state.request_times = defaultdict(deque)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[x.strip() for x in settings.cors_origins.split(",") if x.strip()],
        allow_methods=["GET", "POST"],
        allow_headers=[
            "Content-Type",
            "Authorization",
            "Idempotency-Key",
            "X-Recommendation-Token",
            "Last-Event-ID",
        ],
    )

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if request.url.path.startswith(
            ("/api/v1/health", "/api/v1/runs", "/api/v1/sync/status", "/api/v1/recommendation-jobs")
        ):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/api/v1/health")
    def health():
        with session_factory() as session:
            session.execute(text("SELECT 1"))
        return {
            "status": "ok",
            "version": __version__,
            "database": "ok",
            "llm_configured": settings.llm_configured,
            "embedding_configured": settings.embedding_configured,
            "retrieval_mode": settings.retrieval_mode,
            "steam_discovery_configured": bool(settings.steam_api_key),
            "providers": public_provider_status(session_factory, settings),
        }

    @app.get("/api/v1/stats")
    def stats():
        with session_factory() as session:
            total = session.scalar(select(func.count()).select_from(Mod))
            translated = session.scalar(
                select(func.count()).select_from(Mod).where(Mod.summary_zh.is_not(None))
            )
            latest = session.scalar(select(func.min(Mod.fetched_at)))
        return {
            "total_mods": total,
            "chinese_summaries": translated,
            "last_synced_at": iso(latest),
            "data_source": "steam",
            "discovery_available": bool(settings.steam_api_key),
        }

    @app.get("/api/v1/sync/status")
    def refresh_status():
        return sync_status(session_factory, settings)

    @app.get("/api/v1/mods")
    def mods(
        q: str = Query(default="", max_length=300),
        category: str | None = None,
        sort: Literal["updated", "newest", "popular", "relevant"] = "updated",
        page: int = Query(default=1, ge=1, le=10000),
        page_size: int = Query(default=12, ge=1, le=100),
    ):
        with session_factory() as session:
            return list_mods(
                session, q=q, category=category, sort=sort, page=page, page_size=page_size
            )

    @app.get("/api/v1/mods/{mod_id}")
    def detail(mod_id: str):
        with session_factory() as session:
            mod = session.get(Mod, mod_id)
            if mod is None:
                raise HTTPException(404, "当前目录未收录此 Mod。")
            return serialize_mod(session, mod)

    def check_rate_limit(request):
        now = time.monotonic()
        key = request.client.host if request.client else "unknown"
        # Process-local guard; proxy deployments should also enforce edge rate limiting.
        times = app.state.request_times[key]
        while times and times[0] <= now - 60:
            times.popleft()
        if len(times) >= settings.request_limit_per_minute:
            raise HTTPException(429, "请求较频繁，请稍后再试。", headers={"Retry-After": "60"})
        times.append(now)

    register_recommendation_api(
        app, session_factory, settings, RecommendationInput, check_rate_limit
    )

    @app.post("/api/v1/recommendations")
    async def recommendations(payload: RecommendationInput, request: Request):
        if not payload.query.strip():
            raise HTTPException(422, "描述不能为空。")
        check_rate_limit(request)
        try:
            await asyncio.wait_for(app.state.recommendation_slots.acquire(), timeout=0.2)
        except TimeoutError:
            raise HTTPException(429, "推荐服务正在处理其他请求，请稍后再试。") from None
        try:
            return await recommend(session_factory, settings, payload.query.strip(), payload.limit)
        finally:
            app.state.recommendation_slots.release()

    @app.get("/api/v1/runs/{run_id}")
    def get_run(run_id: str):
        with session_factory() as session:
            run = session.get(RecommendationRun, run_id)
            if run is None:
                raise HTTPException(404, "推荐记录不存在。")
            return run.result or {"run_id": run.id, "status": run.status}

    @app.get("/api/v1/digests/latest")
    def digest():
        with session_factory.begin() as session:
            return build_digest(session)

    @app.post("/api/v1/modlists/validate")
    def validate(payload: ModlistInput):
        with session_factory() as session:
            return validate_modlist(session, payload.mod_ids)

    @app.post("/api/v1/admin/sync")
    async def sync(payload: ModlistInput, authorization: str | None = Header(default=None)):
        if not settings.admin_token:
            raise HTTPException(503, "管理接口未启用。")
        if not authorization or not hmac.compare_digest(
            authorization, "Bearer " + settings.admin_token
        ):
            raise HTTPException(401, "需要管理令牌。")
        from modsage.ingestion import sync_ids
        from modsage.sources.steam import SteamClient, SteamSourceError

        source = SteamClient(api_key=settings.steam_api_key or None)
        try:
            result = await sync_ids(session_factory, source, payload.mod_ids)
            with session_factory.begin() as session:
                build_digest(session, refresh=True)
            return result
        except SteamSourceError:
            raise HTTPException(502, "Steam 数据同步失败；现有数据未被替换。") from None
        finally:
            await source.aclose()

    return app


app = create_app()
