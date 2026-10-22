from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from modsage.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Mod(Base):
    __tablename__ = "mods"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, default="")
    summary_zh: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_status: Mapped[str] = mapped_column(String(32), default="pending")
    summary_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    author: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_url: Mapped[str] = mapped_column(Text)
    preview_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    category: Mapped[str] = mapped_column(String(32), default="other", index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    subscribers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    favorites: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chinese_support: Mapped[str] = mapped_column(String(32), default="unknown")
    compatibility_status: Mapped[str] = mapped_column(String(32), default="unknown")
    dependencies_known: Mapped[bool] = mapped_column(Boolean, default=False)
    content_hash: Mapped[str] = mapped_column(String(64))
    evidence: Mapped[list] = mapped_column(JSON, default=list)


class ModEmbedding(Base):
    """One current document vector per provider/model/dimension namespace."""

    __tablename__ = "mod_embeddings"
    mod_id: Mapped[str] = mapped_column(ForeignKey("mods.id"), primary_key=True)
    namespace: Mapped[str] = mapped_column(String(64), primary_key=True)
    document_hash: Mapped[str] = mapped_column(String(64))
    model: Mapped[str] = mapped_column(String(200))
    dimensions: Mapped[int] = mapped_column(Integer)
    vector: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceSnapshot(Base):
    __tablename__ = "source_snapshots"
    __table_args__ = (UniqueConstraint("mod_id", "payload_hash"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    mod_id: Mapped[str] = mapped_column(ForeignKey("mods.id"), index=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    raw: Mapped[dict] = mapped_column(JSON)


class ModRelation(Base):
    __tablename__ = "mod_relations"
    __table_args__ = (UniqueConstraint("mod_id", "kind", "target_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    mod_id: Mapped[str] = mapped_column(ForeignKey("mods.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    target_id: Mapped[str] = mapped_column(String(32))
    source_url: Mapped[str] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MetricSnapshot(Base):
    __tablename__ = "metric_snapshots"
    __table_args__ = (UniqueConstraint("mod_id", "date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    mod_id: Mapped[str] = mapped_column(ForeignKey("mods.id"), index=True)
    date: Mapped[str] = mapped_column(String(10))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    subscribers: Mapped[int | None] = mapped_column(Integer, nullable=True)
    favorites: Mapped[int | None] = mapped_column(Integer, nullable=True)


class RecommendationRun(Base):
    __tablename__ = "recommendation_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(24))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class RecommendationJob(Base):
    """A capability-protected request, with a fenced durable execution lease."""

    __tablename__ = "recommendation_jobs"
    __table_args__ = (UniqueConstraint("token_hash", "idempotency_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(128))
    query: Mapped[str] = mapped_column(Text)
    limit: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(24), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer)
    request_timeout_seconds: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_event_id: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class RecommendationEvent(Base):
    """Committed snapshots, ordered within their job; independent of SSE connections."""

    __tablename__ = "recommendation_events"
    job_id: Mapped[str] = mapped_column(ForeignKey("recommendation_jobs.id"), primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    data: Mapped[dict] = mapped_column(JSON)


class RecommendationAdmission(Base):
    """A short write lock serializes bounded queue admission on SQLite and PostgreSQL."""

    __tablename__ = "recommendation_admission"
    name: Mapped[str] = mapped_column(String(24), primary_key=True)
    touched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProviderAdmission(Base):
    """A short database write lock protects the shared external-call allowance."""

    __tablename__ = "provider_admission"
    name: Mapped[str] = mapped_column(String(24), primary_key=True)
    touched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ProviderCall(Base):
    """Only bounded operational metadata; never prompts, keys, URLs, or response bodies."""

    __tablename__ = "provider_calls"
    __table_args__ = (
        Index(
            "ix_provider_calls_configuration_reserved",
            "provider",
            "configuration_id",
            "reserved_at",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider: Mapped[str] = mapped_column(String(16))
    operation: Mapped[str] = mapped_column(String(32))
    configuration_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(16), default="started")
    reserved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    returned_model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Digest(Base):
    __tablename__ = "digests"
    date: Mapped[str] = mapped_column(String(10), primary_key=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    result: Mapped[dict] = mapped_column(JSON)


class IngestionRun(Base):
    __tablename__ = "ingestion_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(24))
    mode: Mapped[str] = mapped_column(String(32))
    requested_ids: Mapped[list] = mapped_column(JSON, default=list)
    imported_count: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[list] = mapped_column(JSON, default=list)


class RefreshJob(Base):
    """Durable, bounded refresh with checkpoints and a per-claim fencing token."""

    __tablename__ = "refresh_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True)
    source: Mapped[str] = mapped_column(String(16))
    limit_per_sort: Mapped[int] = mapped_column(Integer)
    enrich_limit: Mapped[int] = mapped_column(Integer)
    request_budget_seconds: Mapped[float] = mapped_column(Float)
    max_attempts: Mapped[int] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(24), default="discovery")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    error_stage: Mapped[str | None] = mapped_column(String(24), nullable=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class RefreshLease(Base):
    """One catalog writer, even when distinct jobs are claimed by several processes."""

    __tablename__ = "refresh_leases"
    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    owner: Mapped[str | None] = mapped_column(String(36), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RefreshWorker(Base):
    __tablename__ = "refresh_workers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    stopped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
