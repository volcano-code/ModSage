"""Opt-in embedding requests and bounded, source-aware catalog backfill."""

import asyncio
import hashlib
import json

from sqlalchemy import func, select

from modsage.models import Mod, ModEmbedding, utcnow
from modsage.provider_config import provider_configuration
from modsage.provider_http import ProviderCall, ProviderRequestError
from modsage.provider_usage import ProviderUsageStore
from modsage.retrieval import document_fields, normalized_vector

DOCUMENT_VERSION = "source-intro-v1"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


class EmbeddingUnavailable(RuntimeError):
    def __init__(self, message="Embedding service is unavailable", *, code="embedding_unavailable"):
        self.code = code
        super().__init__(message)


def document_text(mod) -> str:
    title, tags, description = document_fields(mod)
    return f"Title: {title}\nTags: {tags}\nAuthor description: {description}"


def document_fingerprint(mod) -> str:
    data = [DOCUMENT_VERSION, getattr(mod, "content_hash", ""), document_text(mod)]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()


def embedding_namespace(settings) -> str:
    # A changed endpoint, model, requested dimension, or text schema invalidates reuse.
    # API keys are deliberately absent from this stable identity.
    identity = [
        DOCUMENT_VERSION,
        settings.embedding_base_url.strip().rstrip("/"),
        settings.embedding_model.strip(),
        settings.embedding_dimensions,
    ]
    # An explicitly accepted canonical model is part of the vector identity.
    # Keep the pre-existing namespace when no alias override was configured.
    if settings.embedding_response_model.strip():
        identity.append({"response_model": settings.embedding_response_model.strip()})
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


class EmbeddingClient:
    def __init__(self, settings, client=None, *, usage_store=None, operation="query_embedding"):
        self.settings = settings
        self.client = client
        self.usage_store = usage_store
        self.operation = operation
        self.last_call = None

    async def embed(self, texts: list[str]) -> list[list[float]]:
        call = ProviderCall(self.settings, "embedding", self.operation, self.usage_store)
        self.last_call = call.metadata
        try:
            configuration = provider_configuration(self.settings, "embedding")
            if not configuration["configured"]:
                raise ProviderRequestError(
                    "configuration_invalid" if configuration["issues"] else "not_configured"
                )
            if (
                not isinstance(texts, list)
                or not texts
                or len(texts) > 16
                or any(not isinstance(t, str) or not t.strip() or len(t) > 6000 for t in texts)
            ):
                raise ProviderRequestError("invalid_input")
            try:
                for value in texts:
                    value.encode()
            except UnicodeError:
                raise ProviderRequestError("invalid_input") from None
            body = {
                "model": self.settings.embedding_model.strip(),
                "input": texts,
                "encoding_format": "float",
            }
            if self.settings.embedding_dimensions is not None:
                body["dimensions"] = self.settings.embedding_dimensions
            payload = await call.request(
                client=self.client,
                url=self.settings.embedding_base_url.strip().rstrip("/") + "/embeddings",
                body=body,
                timeout=self.settings.embedding_timeout_seconds,
                response_limit=MAX_RESPONSE_BYTES,
            )
            try:
                if not isinstance(payload.get("data"), list):
                    raise ValueError("Invalid embedding response")
                expected_model = (
                    self.settings.embedding_response_model.strip()
                    or self.settings.embedding_model.strip()
                )
                if payload.get("model", expected_model) != expected_model:
                    raise ProviderRequestError("unexpected_model")
                if len(payload["data"]) != len(texts):
                    raise ValueError("Unexpected embedding count")
                vectors = {}
                dimension = self.settings.embedding_dimensions
                for row in payload["data"]:
                    index = row["index"]
                    if (
                        type(index) is not int
                        or index < 0
                        or index >= len(texts)
                        or index in vectors
                    ):
                        raise ValueError("Invalid embedding index")
                    vector = normalized_vector(row["embedding"], dimensions=dimension)
                    if vector is None:
                        raise ValueError("Invalid embedding vector")
                    dimension = len(vector)
                    vectors[index] = vector
            except (KeyError, TypeError, ValueError, OverflowError):
                raise ProviderRequestError("invalid_response") from None
            error = await call.finish("succeeded")
            if error:
                raise ProviderRequestError(error)
            return [vectors[index] for index in range(len(texts))]
        except asyncio.CancelledError:
            await call.finish("cancelled", "cancelled")
            raise
        except ProviderRequestError as exc:
            code = await call.finish("failed", exc.code)
            message = {
                "configuration_invalid": "Invalid embedding endpoint or configuration",
                "not_configured": "Embedding service is not configured",
                "invalid_input": "Embedding input exceeds the supported batch budget",
                "response_too_large": "Embedding response exceeded its budget",
            }.get(code, "Embedding request failed or returned invalid vectors")
            raise EmbeddingUnavailable(message, code=code) from None


def current_vectors(session, settings, mods, *, dimensions=None) -> tuple[dict, int]:
    """Read only the requested namespace, with source and shape validation."""
    if not mods:
        return {}, 0
    by_id = {mod.id: mod for mod in mods}
    rows = session.scalars(
        select(ModEmbedding).where(
            ModEmbedding.namespace == embedding_namespace(settings),
            ModEmbedding.mod_id.in_(by_id),
        )
    ).all()
    vectors, stale = {}, 0
    for row in rows:
        vector = normalized_vector(
            row.vector, dimensions=dimensions or settings.embedding_dimensions
        )
        if (
            row.model != settings.embedding_model
            or row.document_hash != document_fingerprint(by_id[row.mod_id])
            or vector is None
            or row.dimensions != len(vector)
        ):
            stale += 1
        else:
            vectors[row.mod_id] = vector
    return vectors, stale


def embedding_status(session_factory, settings) -> dict:
    with session_factory() as session:
        total = session.scalar(select(func.count()).select_from(Mod))
        mods = session.scalars(
            select(Mod).order_by(Mod.id).limit(settings.retrieval_catalog_limit)
        ).all()
        vectors, stale = current_vectors(session, settings, mods)
    return {
        "configured": settings.embedding_configured,
        "retrieval_mode": settings.retrieval_mode,
        "model": settings.embedding_model or None,
        "catalog_total": total,
        "scanned": len(mods),
        "indexed": len(vectors),
        "stale": stale,
        "missing": len(mods) - len(vectors) - stale,
        "catalog_limit": settings.retrieval_catalog_limit,
    }


async def backfill_embeddings(session_factory, settings, *, limit=20, batch_size=8, client=None):
    if not 1 <= limit <= 200 or not 1 <= batch_size <= 16:
        raise ValueError("Backfill accepts limit 1..200 and batch size 1..16")
    if not settings.embedding_configured:
        raise EmbeddingUnavailable("Embedding service is not configured")
    with session_factory() as session:
        total = session.scalar(select(func.count()).select_from(Mod))
        mods = session.scalars(
            select(Mod).order_by(Mod.id).limit(settings.retrieval_catalog_limit)
        ).all()
        fresh, _ = current_vectors(session, settings, mods)
        pending = [mod for mod in mods if mod.id not in fresh]
        snapshots = [
            (mod.id, document_fingerprint(mod), document_text(mod)) for mod in pending[:limit]
        ]
    result = {
        "status": "completed",
        "scanned": len(mods),
        "generated": 0,
        "skipped": len(fresh),
        "source_changed": 0,
        "remaining": len(pending),
        "model": settings.embedding_model,
        "errors": [],
        "warnings": [],
    }
    if total > len(mods):
        result["warnings"].append("目录超过本次扫描上限；未扫描条目不计入索引覆盖。")
    provider = client or EmbeddingClient(
        settings,
        usage_store=ProviderUsageStore(session_factory, settings),
        operation="document_embedding",
    )
    namespace = embedding_namespace(settings)
    for start in range(0, len(snapshots), batch_size):
        batch = snapshots[start : start + batch_size]
        try:
            # No session/transaction survives across this network request.
            async with asyncio.timeout(settings.embedding_timeout_seconds):
                vectors = await provider.embed([row[2] for row in batch])
            if len(vectors) != len(batch):
                raise EmbeddingUnavailable("Unexpected embedding count")
            normalized = [
                normalized_vector(vector, dimensions=settings.embedding_dimensions)
                for vector in vectors
            ]
            if (
                any(vector is None for vector in normalized)
                or len({len(v) for v in normalized if v is not None}) != 1
            ):
                raise EmbeddingUnavailable("Invalid embedding vector")
        except (EmbeddingUnavailable, TimeoutError):
            result["status"] = "partial" if result["generated"] else "failed"
            result["errors"].append("向量服务不可用或返回无效结果；本批未写入，可重试未完成条目。")
            break
        with session_factory.begin() as session:
            for (mod_id, fingerprint, _), vector in zip(batch, normalized, strict=True):
                # PG locks the source row while comparing and committing its derived record.
                # SQLite serializes writes; every later read still revalidates the fingerprint.
                current = session.get(Mod, mod_id, with_for_update=True)
                if current is None or document_fingerprint(current) != fingerprint:
                    result["source_changed"] += 1
                    continue
                row = session.get(ModEmbedding, (mod_id, namespace))
                if row is None:
                    row = ModEmbedding(mod_id=mod_id, namespace=namespace)
                    session.add(row)
                row.document_hash = fingerprint
                row.model = settings.embedding_model
                row.dimensions = len(vector)
                row.vector = vector
                row.created_at = utcnow()
                result["generated"] += 1
                result["remaining"] -= 1
    if result["source_changed"]:
        if result["status"] == "completed":
            result["status"] = "partial"
        result["warnings"].append("部分条目在生成期间更新，未提交过期向量；请再次运行回填。")
    return result
