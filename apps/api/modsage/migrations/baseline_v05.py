"""Frozen v0.5 schema. Never edit this baseline when changing live ORM models."""

import sqlalchemy as sa


def baseline_metadata() -> sa.MetaData:
    metadata = sa.MetaData()
    sa.Table(
        "digests",
        metadata,
        sa.Column("date", sa.String(length=10), primary_key=True, nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
    )
    sa.Table(
        "ingestion_runs",
        metadata,
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("requested_ids", sa.JSON(), nullable=False),
        sa.Column("imported_count", sa.Integer(), nullable=False),
        sa.Column("warnings", sa.JSON(), nullable=False),
    )
    sa.Table(
        "mods",
        metadata,
        sa.Column("id", sa.String(length=32), primary_key=True, nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("summary_zh", sa.Text(), nullable=True),
        sa.Column("summary_status", sa.String(length=32), nullable=False),
        sa.Column("summary_hash", sa.String(length=64), nullable=True),
        sa.Column("summary_model", sa.String(length=120), nullable=True),
        sa.Column("author", sa.String(length=200), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("preview_url", sa.Text(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subscribers", sa.Integer(), nullable=True),
        sa.Column("favorites", sa.Integer(), nullable=True),
        sa.Column("chinese_support", sa.String(length=32), nullable=False),
        sa.Column("compatibility_status", sa.String(length=32), nullable=False),
        sa.Column("dependencies_known", sa.Boolean(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
    )
    sa.Table(
        "provider_admission",
        metadata,
        sa.Column("name", sa.String(length=24), primary_key=True, nullable=False),
        sa.Column("touched_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "provider_calls",
        metadata,
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("configuration_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=48), nullable=True),
        sa.Column("returned_model", sa.String(length=200), nullable=True),
        sa.Column("request_id", sa.String(length=200), nullable=True),
        sa.Column("finish_reason", sa.String(length=32), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
    )
    sa.Table(
        "recommendation_admission",
        metadata,
        sa.Column("name", sa.String(length=24), primary_key=True, nullable=False),
        sa.Column("touched_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "recommendation_jobs",
        metadata,
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("limit", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("stage", sa.String(length=24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("request_timeout_seconds", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.String(length=36), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_id", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=48), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.UniqueConstraint("token_hash", "idempotency_key"),
    )
    sa.Table(
        "recommendation_runs",
        metadata,
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", sa.JSON(), nullable=False),
    )
    sa.Table(
        "refresh_jobs",
        metadata,
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("limit_per_sort", sa.Integer(), nullable=False),
        sa.Column("enrich_limit", sa.Integer(), nullable=False),
        sa.Column("request_budget_seconds", sa.Float(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("stage", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=36), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=48), nullable=True),
        sa.Column("error_stage", sa.String(length=24), nullable=True),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.UniqueConstraint("idempotency_key"),
    )
    sa.Table(
        "refresh_leases",
        metadata,
        sa.Column("name", sa.String(length=32), primary_key=True, nullable=False),
        sa.Column("owner", sa.String(length=36), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    sa.Table(
        "refresh_workers",
        metadata,
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stopped_at", sa.DateTime(timezone=True), nullable=True),
    )
    sa.Table(
        "metric_snapshots",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("mod_id", sa.String(length=32), sa.ForeignKey("mods.id"), nullable=False),
        sa.Column("date", sa.String(length=10), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("subscribers", sa.Integer(), nullable=True),
        sa.Column("favorites", sa.Integer(), nullable=True),
        sa.UniqueConstraint("mod_id", "date"),
    )
    sa.Table(
        "mod_embeddings",
        metadata,
        sa.Column(
            "mod_id",
            sa.String(length=32),
            sa.ForeignKey("mods.id"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("namespace", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("document_hash", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("vector", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    sa.Table(
        "mod_relations",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("mod_id", sa.String(length=32), sa.ForeignKey("mods.id"), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=32), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("mod_id", "kind", "target_id"),
    )
    sa.Table(
        "recommendation_events",
        metadata,
        sa.Column(
            "job_id",
            sa.String(length=36),
            sa.ForeignKey("recommendation_jobs.id"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("sequence", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
    )
    sa.Table(
        "source_snapshots",
        metadata,
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("mod_id", sa.String(length=32), sa.ForeignKey("mods.id"), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.UniqueConstraint("mod_id", "payload_hash"),
    )
    sa.Index("ix_mods_category", metadata.tables["mods"].c["category"], unique=False)
    sa.Index("ix_mods_updated_at", metadata.tables["mods"].c["updated_at"], unique=False)
    sa.Index(
        "ix_provider_calls_configuration_id",
        metadata.tables["provider_calls"].c["configuration_id"],
        unique=False,
    )
    sa.Index(
        "ix_provider_calls_configuration_reserved",
        metadata.tables["provider_calls"].c["provider"],
        metadata.tables["provider_calls"].c["configuration_id"],
        metadata.tables["provider_calls"].c["reserved_at"],
        unique=False,
    )
    sa.Index(
        "ix_provider_calls_reserved_at",
        metadata.tables["provider_calls"].c["reserved_at"],
        unique=False,
    )
    sa.Index(
        "ix_recommendation_jobs_status",
        metadata.tables["recommendation_jobs"].c["status"],
        unique=False,
    )
    sa.Index(
        "ix_refresh_jobs_next_attempt_at",
        metadata.tables["refresh_jobs"].c["next_attempt_at"],
        unique=False,
    )
    sa.Index("ix_refresh_jobs_status", metadata.tables["refresh_jobs"].c["status"], unique=False)
    sa.Index(
        "ix_refresh_workers_last_seen_at",
        metadata.tables["refresh_workers"].c["last_seen_at"],
        unique=False,
    )
    sa.Index(
        "ix_metric_snapshots_mod_id", metadata.tables["metric_snapshots"].c["mod_id"], unique=False
    )
    sa.Index("ix_mod_relations_mod_id", metadata.tables["mod_relations"].c["mod_id"], unique=False)
    sa.Index(
        "ix_source_snapshots_mod_id", metadata.tables["source_snapshots"].c["mod_id"], unique=False
    )
    return metadata
