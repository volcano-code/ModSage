import { ApiError, api } from "./api";
import { SseDecoder } from "./sse";
import type { RecommendationJob, RecommendationJobStatus, RecommendationRun } from "./types";

export const JOB_HISTORY_KEY = "modsage:recommendation-jobs:v1";
export const JOB_HISTORY_LIMIT = 20;

export interface LocalJobRecord {
  key: string;
  token: string;
  id: string | null;
  query: string;
  limit: number;
  created_at: string;
  last_status?: RecommendationJobStatus;
}

export interface LocalJobHistory {
  version: 1;
  selected_key: string | null;
  items: LocalJobRecord[];
}

const statuses = ["queued", "running", "completed", "failed", "cancelled"];
const stages = ["queued", "understand", "retrieve", "verify", "completed", "failed", "cancelled"];
const safeKey = /^[A-Za-z0-9_-]{32,128}$/;
const jobId = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
const object = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
const integer = (value: unknown, min: number, max: number): value is number => typeof value === "number" && Number.isSafeInteger(value) && value >= min && value <= max;
const timestamp = (value: unknown): value is string => typeof value === "string" && value.length < 50 && Number.isFinite(Date.parse(value));

export function terminalJob(job: RecommendationJob): boolean {
  return ["completed", "failed", "cancelled"].includes(job.status);
}

function validRun(value: unknown): value is RecommendationRun {
  return object(value) && typeof value.run_id === "string" && typeof value.status === "string"
    && typeof value.engine === "string" && object(value.constraints)
    && Array.isArray(value.items) && Array.isArray(value.warnings) && value.warnings.every((item) => typeof item === "string")
    && value.items.every((item) => object(item) && object(item.mod) && typeof item.mod.id === "string"
      && typeof item.mod.title === "string" && Array.isArray(item.reasons) && Array.isArray(item.caveats));
}

export function readJobSnapshot(value: unknown, expectedId?: string | null): RecommendationJob {
  if (!object(value) || typeof value.id !== "string" || !jobId.test(value.id) || (expectedId && value.id !== expectedId)
    || typeof value.query !== "string" || value.query.length > 1000 || value.query.trim().length < 2
    || !integer(value.limit, 1, 10) || !statuses.includes(String(value.status)) || !stages.includes(String(value.stage))
    || !integer(value.attempts, 0, 100) || !integer(value.max_attempts, 1, 100)
    || !integer(value.last_event_id, 0, Number.MAX_SAFE_INTEGER) || !timestamp(value.created_at)
    || !(value.started_at === null || timestamp(value.started_at)) || !(value.completed_at === null || timestamp(value.completed_at))
    || !(value.error_code === null || typeof value.error_code === "string")
    || !(value.result === null || validRun(value.result))) {
    throw new Error("暂时无法读取任务状态，请重新连接。");
  }
  return value as unknown as RecommendationJob;
}

function validRecord(value: unknown): value is LocalJobRecord {
  return object(value) && typeof value.key === "string" && safeKey.test(value.key)
    && typeof value.token === "string" && safeKey.test(value.token)
    && (value.id === null || typeof value.id === "string" && jobId.test(value.id))
    && typeof value.query === "string" && value.query.trim().length >= 2 && value.query.length <= 1000
    && integer(value.limit, 1, 10) && timestamp(value.created_at)
    && (value.last_status === undefined || statuses.includes(String(value.last_status)));
}

export function loadJobHistory(): { history: LocalJobHistory; warning: string | null } {
  const empty: LocalJobHistory = { version: 1, selected_key: null, items: [] };
  try {
    const raw = localStorage.getItem(JOB_HISTORY_KEY);
    if (!raw) return { history: empty, warning: null };
    if (raw.length > 131_072) throw new Error("oversized");
    const value: unknown = JSON.parse(raw);
    if (!object(value) || value.version !== 1 || !Array.isArray(value.items)) throw new Error("invalid");
    const unique = new Set<string>();
    const items = value.items.filter(validRecord).filter((item) => {
      if (unique.has(item.key) || item.id && unique.has(item.id)) return false;
      unique.add(item.key); if (item.id) unique.add(item.id); return true;
    }).slice(0, JOB_HISTORY_LIMIT);
    return {
      history: { version: 1, items, selected_key: items.some((item) => item.key === value.selected_key) ? value.selected_key as string : null },
      warning: items.length === value.items.length ? null : "部分本机记录无法读取，已跳过；有效记录仍可继续使用。",
    };
  } catch {
    return { history: empty, warning: "暂时无法读取本机推荐记录。新任务仍可使用，但刷新后的恢复可能受限。" };
  }
}

export function saveJobHistory(history: LocalJobHistory): boolean {
  try { localStorage.setItem(JOB_HISTORY_KEY, JSON.stringify(history)); return true; }
  catch { return false; }
}

export function newJobRecord(query: string): LocalJobRecord {
  const bytes = crypto.getRandomValues(new Uint8Array(32));
  return {
    key: crypto.randomUUID(), token: Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join(""),
    id: null, query: query.trim(), limit: 5, created_at: new Date().toISOString(),
  };
}

export async function requestJob(record: LocalJobRecord, action: "create" | "get" | "cancel" | "retry", signal: AbortSignal): Promise<RecommendationJob> {
  const isCreate = action === "create";
  const path = `/recommendation-jobs${isCreate ? "" : `/${record.id}${action === "get" ? "" : `/${action}`}`}`;
  const value = await api<unknown>(path, {
    method: action === "get" ? "GET" : "POST",
    redirect: "error",
    headers: { "X-Recommendation-Token": record.token, ...(isCreate ? { "Idempotency-Key": record.key } : {}) },
    ...(action !== "get" ? { body: JSON.stringify(isCreate ? { query: record.query, limit: record.limit } : {}) } : {}),
    signal: AbortSignal.any([signal, AbortSignal.timeout(45_000)]),
  });
  return readJobSnapshot(value, record.id);
}

export async function streamJobEvents(record: LocalJobRecord, after: number, signal: AbortSignal, onSnapshot: (job: RecommendationJob) => void, onOpen: () => void): Promise<void> {
  const response = await fetch(`/api/v1/recommendation-jobs/${record.id}/events`, {
    headers: { Accept: "text/event-stream", "X-Recommendation-Token": record.token, "Last-Event-ID": String(after) },
    cache: "no-store", redirect: "error", signal: AbortSignal.any([signal, AbortSignal.timeout(35_000)]),
  });
  if (!response.ok) {
    await response.body?.cancel();
    throw new ApiError("暂时无法连接任务进度。", response.status);
  }
  if (!response.headers.get("content-type")?.includes("text/event-stream") || !response.body) {
    await response.body?.cancel();
    throw new Error("暂时无法读取实时进度。");
  }
  onOpen();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseDecoder();
  let cursor = after;
  try {
    while (!signal.aborted) {
      const { value, done } = await reader.read();
      const events = parser.push(done ? decoder.decode() : decoder.decode(value, { stream: true }));
      for (const event of events) {
        if (event.event !== "progress") continue;
        if (!/^[1-9][0-9]{0,15}$/.test(event.id)) throw new Error("任务进度标识无效。");
        const eventId = Number(event.id);
        if (!Number.isSafeInteger(eventId)) throw new Error("任务进度标识无效。");
        if (eventId <= cursor) continue;
        const job = readJobSnapshot(JSON.parse(event.data), record.id);
        if (job.last_event_id !== eventId) throw new Error("任务进度暂时不一致。");
        cursor = eventId;
        onSnapshot(job);
        // An old failed attempt can precede a newer retry in the same replay stream.
        // Consume through EOF; the server closes after replaying the latest terminal state.
      }
      if (done) return;
    }
  } finally {
    await reader.cancel().catch(() => undefined);
    reader.releaseLock();
  }
}

export function jobFailureMessage(failure: unknown, fallback = "暂时无法读取任务，请稍后重新连接。"): string {
  if (failure instanceof ApiError) {
    if (failure.status === 401 || failure.status === 403 || failure.status === 404) return "无法读取这条任务，可能已不存在或本机记录不完整。";
    if (failure.status === 429) return "当前请求较多，请稍后再试。";
    if (failure.status === 409) return "任务状态已经变化，请重新连接查看最新状态。";
    if (failure.status === 422 || failure.status === 400) return "提交内容未通过检查，请调整描述后再试。";
  }
  return fallback;
}
