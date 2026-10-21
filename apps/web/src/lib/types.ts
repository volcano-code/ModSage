export interface Evidence {
  id: string;
  field: string;
  value: unknown;
  source_url: string;
  excerpt: string;
  observed_at: string;
}

export interface ModRelation {
  kind: string;
  target_id: string;
  source_url: string;
}

export interface Mod {
  id: string;
  title: string;
  summary_zh: string | null;
  description_excerpt: string;
  author: string | null;
  source_url: string;
  preview_url: string | null;
  tags: string[];
  category: string;
  published_at: string | null;
  updated_at: string | null;
  fetched_at: string;
  subscribers: number | null;
  favorites: number | null;
  chinese_support: "unknown" | "author_claimed" | "translation_available";
  summary_status: "pending" | "generated" | "source_chinese";
  compatibility_status: "unknown" | "author_claimed";
  dependencies_known: boolean;
  evidence: Evidence[];
  relations: ModRelation[];
  source_kind: "steam";
}

export interface ModPage {
  items: Mod[];
  total: number;
  page: number;
  page_size: number;
}

export interface Stats {
  total_mods: number;
  chinese_summaries: number;
  last_synced_at: string | null;
  data_source: string;
  discovery_available: boolean;
}

export interface Health {
  status: string;
  version: string;
  database: string;
  llm_configured: boolean;
  steam_discovery_configured: boolean;
  embedding_configured?: boolean;
  retrieval_mode?: "bm25" | "hybrid";
}

export interface SyncStatus {
  latest_job: {
    id: string;
    status: "queued" | "running" | "retry_wait" | "succeeded" | "partial" | "failed";
    stage: string;
    source: string;
    attempts: number;
    max_attempts: number;
    created_at: string;
    started_at: string | null;
    completed_at: string | null;
    next_attempt_at: string | null;
    error_code: string | null;
    error_stage: string | null;
    counts: { discovered: number; imported: number; enriched: number };
  } | null;
  last_successful_refresh_at: string | null;
  schedule: { enabled: boolean; timezone: string; time: string; next_run_at: string | null };
  worker: { status: "recently_seen" | "stopped" | "stale" | "unknown"; last_seen_at: string | null };
}

export interface Digest {
  date: string;
  generated_at: string;
  source_cutoff: string | null;
  sections: { key: string; title: string; items: Mod[] }[];
  warnings: string[];
}

export interface Recommendation {
  mod: Mod;
  score: number;
  reasons: string[];
  caveats: string[];
  retrieval?: {
    lexical_score: number;
    semantic_score: number | null;
    matched_terms: string[];
    channels: ("lexical" | "semantic")[];
  };
}

export interface RecommendationRun {
  run_id: string;
  status: string;
  engine: string;
  constraints: Record<string, unknown>;
  items: Recommendation[];
  warnings: string[];
  retrieval?: {
    method: "bm25" | "hybrid_rrf";
    requested_method: "bm25" | "hybrid_rrf";
    candidate_count: number;
    eligible_count: number;
    indexed_count: number;
    stale_count: number;
  };
}

export type RecommendationJobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface RecommendationJob {
  id: string;
  query: string;
  limit: number;
  status: RecommendationJobStatus;
  stage: "queued" | "understand" | "retrieve" | "verify" | "completed" | "failed" | "cancelled";
  attempts: number;
  max_attempts: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  last_event_id: number;
  error_code: string | null;
  result: RecommendationRun | null;
}

export interface Validation {
  mod_ids: string[];
  missing_dependencies: { mod_id: string; required_id: string }[];
  known_conflicts: unknown[];
  unknown_compatibility: string[];
  warnings: string[];
}

export interface SavedMod {
  id: string;
  title: string;
}
