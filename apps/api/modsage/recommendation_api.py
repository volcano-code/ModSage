"""HTTP and replayable SSE boundary for protected recommendation jobs."""

import asyncio
import json
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.exc import SQLAlchemyError

from modsage.recommendation_jobs import (
    TERMINAL,
    RecommendationJobConflict,
    RecommendationJobNotFound,
    RecommendationQueueFull,
    cancel_recommendation_job,
    enqueue_recommendation_job,
    get_recommendation_job,
    parse_event_cursor,
    recommendation_events,
    retry_recommendation_job,
    token_fingerprint,
)


class _RecommendationStream(StreamingResponse):
    """Release admission even when transport failure precedes the first body yield."""

    def __init__(self, *args, release, **kwargs):
        super().__init__(*args, **kwargs)
        self._release = release

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._release()


def register_recommendation_api(app, session_factory, settings, input_model, rate_limit):
    streams = asyncio.Semaphore(settings.recommendation_stream_limit)

    async def database_call(operation, *args, **kwargs):
        try:
            return await asyncio.to_thread(operation, *args, **kwargs)
        except SQLAlchemyError:
            # SQL exceptions can contain request parameters; never return or log them here.
            raise HTTPException(
                503, "推荐任务存储暂时不可用，请稍后重试。", headers={"Retry-After": "2"}
            ) from None

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        if request.url.path.startswith("/api/v1/recommendation-jobs"):
            return JSONResponse({"detail": "推荐任务请求格式无效。"}, status_code=422)
        return await request_validation_exception_handler(request, exc)

    def token_from(request):
        token = request.headers.get("X-Recommendation-Token", "")
        try:
            token_fingerprint(token)
        except ValueError:
            # Do not use Pydantic header validation: its error detail can echo credentials.
            raise HTTPException(401, "需要有效的推荐任务访问凭证。") from None
        return token

    @app.exception_handler(RecommendationJobNotFound)
    async def not_found(request, exc):
        return JSONResponse({"detail": "推荐任务不存在或访问凭证无效。"}, status_code=404)

    @app.exception_handler(RecommendationJobConflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(RecommendationQueueFull)
    async def queue_full(request, exc):
        return JSONResponse(
            {"detail": "推荐队列暂时已满，请稍后重试。"},
            status_code=429,
            headers={"Retry-After": "10"},
        )

    # The model is supplied by create_app to keep the synchronous contract identical.
    @app.post("/api/v1/recommendation-jobs", status_code=202)
    async def create_job(payload: input_model, request: Request):
        token = token_from(request)
        rate_limit(request)
        try:
            return await database_call(
                enqueue_recommendation_job,
                session_factory,
                settings,
                payload.query,
                payload.limit,
                token=token,
                idempotency_key=request.headers.get("Idempotency-Key", ""),
            )
        except ValueError:
            raise HTTPException(422, "请求内容或幂等标识无效。") from None

    @app.get("/api/v1/recommendation-jobs/{job_id}")
    async def get_job(job_id: UUID, request: Request):
        return await database_call(
            get_recommendation_job, session_factory, str(job_id), token_from(request)
        )

    @app.post("/api/v1/recommendation-jobs/{job_id}/cancel")
    async def cancel_job(job_id: UUID, request: Request):
        return await database_call(
            cancel_recommendation_job, session_factory, str(job_id), token_from(request)
        )

    @app.post("/api/v1/recommendation-jobs/{job_id}/retry", status_code=202)
    async def retry_job(job_id: UUID, request: Request):
        token = token_from(request)
        rate_limit(request)
        return await database_call(
            retry_recommendation_job, session_factory, settings, str(job_id), token
        )

    @app.get("/api/v1/recommendation-jobs/{job_id}/events")
    async def events(job_id: UUID, request: Request, after: str | None = None):
        token = token_from(request)
        try:
            cursor = max(
                parse_event_cursor(after), parse_event_cursor(request.headers.get("Last-Event-ID"))
            )
        except ValueError:
            raise HTTPException(422, "事件游标必须是有界非负整数。") from None
        identifier = str(job_id)
        initial = await database_call(get_recommendation_job, session_factory, identifier, token)
        if cursor > initial["last_event_id"]:
            raise RecommendationJobConflict("事件游标超出当前记录，请重新读取任务状态。")
        try:
            await asyncio.wait_for(streams.acquire(), timeout=0.2)
        except TimeoutError:
            raise HTTPException(
                429, "进度连接较多，请稍后重新连接。", headers={"Retry-After": "2"}
            ) from None

        released = False

        def release():
            nonlocal released
            if not released:
                released = True
                streams.release()

        async def stream():
            current = cursor
            loop = asyncio.get_running_loop()
            deadline = loop.time() + settings.recommendation_sse_seconds
            last_heartbeat = loop.time()
            try:
                yield "retry: 1000\n\n"
                while loop.time() < deadline:
                    if await request.is_disconnected():
                        break
                    batch = await asyncio.to_thread(
                        recommendation_events, session_factory, identifier, token, after=current
                    )
                    for event in batch:
                        current = event["id"]
                        payload = json.dumps(
                            event["data"], ensure_ascii=False, separators=(",", ":")
                        )
                        yield f"id: {current}\nevent: progress\ndata: {payload}\n\n"
                    latest = await asyncio.to_thread(
                        get_recommendation_job, session_factory, identifier, token
                    )
                    # Historical failed attempts do not end a stream if a newer retry exists.
                    if latest["status"] in TERMINAL and current >= latest["last_event_id"]:
                        break
                    if loop.time() - last_heartbeat >= 5:
                        yield ": heartbeat\n\n"
                        last_heartbeat = loop.time()
                    await asyncio.sleep(min(0.5, settings.recommendation_poll_seconds))
            except SQLAlchemyError:
                # Closing only the transport lets the client reconnect; the job is unaffected.
                yield ": reconnect\n\n"
            finally:
                release()

        return _RecommendationStream(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
            release=release,
        )
