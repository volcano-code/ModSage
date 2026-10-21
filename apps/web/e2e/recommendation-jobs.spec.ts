import { randomBytes, randomUUID } from "node:crypto";
import { expect, test, type Page } from "@playwright/test";
import type { RecommendationRun } from "../src/lib/types";

const root = "/api/v1/recommendation-jobs";
const historyKey = "modsage:recommendation-jobs:v1";

type JobStatus = "queued" | "running" | "completed" | "failed" | "cancelled";
interface Job {
  id: string;
  query: string;
  limit: number;
  status: JobStatus;
  stage: string;
  attempts: number;
  max_attempts: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  last_event_id: number;
  error_code: string | null;
  result: RecommendationRun | null;
}

function parseEvents(body: string): { id: number; data: Job }[] {
  return body.replaceAll("\r\n", "\n").split("\n\n").flatMap((frame) => {
    const lines = frame.split("\n");
    const data = lines.filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart());
    if (!data.length) return [];
    expect(lines).toContain("event: progress");
    const id = Number(lines.find((line) => line.startsWith("id:"))?.slice(3).trim());
    expect(Number.isSafeInteger(id) && id > 0).toBeTruthy();
    return [{ id, data: JSON.parse(data.join("\n")) as Job }];
  });
}

test("real durable API preserves idempotency, token privacy and numbered SSE replay through the proxy", async ({ request }) => {
  const token = randomBytes(32).toString("hex");
  const headers = { "X-Recommendation-Token": token, "Idempotency-Key": randomUUID() };
  const payload = { query: "帝国增加火炮，不要大修", limit: 3 };
  const created = await request.post(root, { headers, data: payload });
  expect(created.status()).toBe(202);
  const initial: Job = await created.json();
  expect(JSON.stringify(initial)).not.toContain(token);
  expect(initial).not.toHaveProperty("token_hash");
  const repeat = await request.post(root, { headers, data: payload });
  expect(repeat.status()).toBe(202);
  expect((await repeat.json()).id).toBe(initial.id);
  const conflict = await request.post(root, { headers, data: { ...payload, query: "另一条需求" } });
  expect(conflict.status()).toBe(409);
  const url = `${root}/${initial.id}`;
  await expect.poll(async () => (await (await request.get(url, { headers })).json()).status, {
    timeout: 15_000,
  }).toBe("completed");

  expect((await request.get(url)).status()).toBe(401);
  expect((await request.get(url, { headers: { "X-Recommendation-Token": "f".repeat(64) } })).status()).toBe(404);
  expect((await request.get(`${url}?token=${token}`)).status()).toBe(401);
  expect((await request.post(`${url}/cancel`, { data: {} })).status()).toBe(401);
  expect((await request.get(`${url}/events`, { headers: { "X-Recommendation-Token": "e".repeat(64) } })).status()).toBe(404);

  const stream = await request.get(`${url}/events`, { headers });
  expect(stream.status()).toBe(200);
  expect(stream.headers()["content-type"]).toContain("text/event-stream");
  expect(stream.headers()["cache-control"]).toContain("no-store");
  const events = parseEvents(await stream.text());
  expect(events.length).toBeGreaterThanOrEqual(5);
  const ids = events.map((event) => event.id);
  expect(ids).toEqual([...new Set(ids)].sort((a, b) => a - b));
  expect(events.map((event) => event.data.stage)).toEqual(expect.arrayContaining([
    "queued", "understand", "retrieve", "verify", "completed",
  ]));
  const final = events.at(-1)!;
  expect(final.data.status).toBe("completed");
  expect(final.data.last_event_id).toBe(final.id);
  expect(final.data.result?.retrieval?.method).toBe("bm25");
  expect((await request.get(`/api/v1/runs/${final.data.result?.run_id}`)).status()).toBe(404);

  const replay = await request.get(`${url}/events?after=0`, {
    headers: { ...headers, "Last-Event-ID": String(ids.at(-2)) },
  });
  expect(replay.status()).toBe(200);
  expect(parseEvents(await replay.text()).map((event) => event.id)).toEqual([final.id]);
  expect((await request.get(`${url}/events?after=${final.id + 1}`, { headers })).status()).toBe(409);
  expect((await request.get(`${url}/events`, { headers: { ...headers, "Last-Event-ID": "-1" } })).status()).toBe(422);
});

// These UI fixtures intentionally control network timing. They never enter either application database.
function fixture(query: string, status: JobStatus = "queued"): Job {
  const now = new Date().toISOString();
  return {
    id: randomUUID(), query, limit: 5, status, stage: status,
    attempts: status === "queued" ? 0 : 1, max_attempts: 3,
    created_at: now, started_at: status === "queued" ? null : now,
    completed_at: ["completed", "failed", "cancelled"].includes(status) ? now : null,
    last_event_id: 1, error_code: status === "failed" ? "request_failed" : null, result: null,
  };
}

function complete(job: Job): Job {
  return {
    ...job, status: "completed", stage: "completed", completed_at: new Date().toISOString(),
    attempts: Math.max(1, job.attempts), last_event_id: job.last_event_id + 1, error_code: null,
    result: {
      run_id: job.id, status: "completed", engine: "rules", constraints: {}, items: [],
      warnings: ["E2E UI fixture: no catalogue records are inserted."],
      retrieval: { method: "bm25", requested_method: "bm25", candidate_count: 0, eligible_count: 0, indexed_count: 0, stale_count: 0 },
    },
  };
}

async function storeHistory(page: Page, job: Job, token: string, key: string) {
  await page.addInitScript(({ storageKey, savedJob, savedToken, savedKey }) => {
    localStorage.setItem(storageKey, JSON.stringify({
      version: 1, selected_key: savedKey,
      items: [{ key: savedKey, token: savedToken, id: savedJob.id, query: savedJob.query,
        limit: savedJob.limit, created_at: savedJob.created_at, last_status: savedJob.status }],
    }));
  }, { storageKey: historyKey, savedJob: job, savedToken: token, savedKey: key });
}

test("UI fixture: queued task survives reload and cancellation waits for server confirmation", async ({ page }) => {
  let job = fixture("E2E 等待中的帝国推荐");
  let creates = 0;
  let cancelRequested = false;
  let confirmCancel: () => void = () => {};
  const cancellation = new Promise<void>((resolve) => { confirmCancel = resolve; });
  await page.route(`**${root}**`, async (route) => {
    const path = new URL(route.request().url()).pathname;
    expect(route.request().headers()["x-recommendation-token"]).toMatch(/^[A-Za-z0-9_-]{32,128}$/);
    if (path === root) {
      creates += 1;
      await route.fulfill({ status: 202, json: job });
    } else if (path.endsWith("/cancel")) {
      cancelRequested = true;
      await cancellation;
      job = { ...job, status: "cancelled", stage: "cancelled", completed_at: new Date().toISOString(), last_event_id: 2 };
      await route.fulfill({ json: job });
    } else if (path.endsWith("/events")) {
      await route.fulfill({ contentType: "text/event-stream", body: `id: ${job.last_event_id}\nevent: progress\ndata: ${JSON.stringify(job)}\n\n` });
    } else {
      await route.fulfill({ json: job });
    }
  });
  await page.goto("/recommend");
  await page.getByLabel("你的战役构想").fill(job.query);
  await page.getByRole("button", { name: "寻找适合的模组", exact: true }).click();
  const progress = page.getByRole("region", { name: "推荐任务进度" });
  await expect(progress).toBeVisible();
  await expect(page.getByRole("button", { name: "取消任务", exact: true })).toBeVisible();
  await page.reload();
  await expect(progress).toBeVisible();
  expect(creates).toBe(1);
  await page.getByRole("button", { name: "取消任务", exact: true }).click();
  try {
    await expect.poll(() => cancelRequested).toBeTruthy();
    await expect(progress).not.toContainText("已取消");
  } finally {
    confirmCancel();
  }
  await expect(progress).toContainText("已取消");
  await expect(page.getByRole("button", { name: "取消任务", exact: true })).toHaveCount(0);
  await page.reload();
  await expect(progress).toContainText("已取消");
  expect(creates).toBe(1);
});

test("UI fixture: a failed persisted task retries the same protected job and shows its final result", async ({ page }) => {
  let job = fixture("E2E 失败后重新推荐帝国", "failed");
  const token = randomBytes(32).toString("hex");
  let retries = 0;
  await storeHistory(page, job, token, randomUUID());
  await page.route(`**${root}**`, async (route) => {
    expect(route.request().headers()["x-recommendation-token"]).toBe(token);
    const path = new URL(route.request().url()).pathname;
    expect(path).not.toBe(root);
    if (path.endsWith("/retry")) {
      retries += 1;
      job = { ...job, status: "queued", stage: "queued", completed_at: null, error_code: null, last_event_id: 2 };
      await route.fulfill({ json: job });
    } else if (path.endsWith("/events")) {
      job = complete({ ...job, attempts: 2 });
      await route.fulfill({ contentType: "text/event-stream", body: `id: ${job.last_event_id}\nevent: progress\ndata: ${JSON.stringify(job)}\n\n` });
    } else {
      await route.fulfill({ json: job });
    }
  });
  await page.goto("/recommend");
  await page.getByRole("button", { name: "重试任务", exact: true }).click();
  await expect(page.getByRole("region", { name: "推荐结果" })).toBeVisible();
  await expect(page.getByRole("region", { name: "推荐任务进度" })).toContainText("已完成");
  expect(retries).toBe(1);
});

test("UI fixture: an interrupted stream replays a historical failure and newer retry from its last applied event", async ({ page }) => {
  const initial = { ...fixture("E2E 事件断线后继续推荐帝国", "running"), stage: "understand", last_event_id: 2 };
  const retrieved = { ...initial, stage: "retrieve", last_event_id: 3 };
  const failed: Job = { ...retrieved, status: "failed", stage: "failed", last_event_id: 4,
    completed_at: new Date().toISOString(), error_code: "WorkerError" };
  const retried: Job = { ...failed, status: "queued", stage: "queued", last_event_id: 5,
    started_at: null, completed_at: null, error_code: null };
  const verified: Job = { ...retried, status: "running", stage: "verify", last_event_id: 6,
    attempts: 2, started_at: new Date().toISOString() };
  const finished = complete(verified);
  const token = randomBytes(32).toString("hex");
  const cursors: string[] = [];
  await storeHistory(page, initial, token, randomUUID());
  await page.route(`**${root}**`, async (route) => {
    expect(route.request().headers()["x-recommendation-token"]).toBe(token);
    const path = new URL(route.request().url()).pathname;
    expect(route.request().method()).toBe("GET");
    if (path.endsWith("/events")) {
      cursors.push(route.request().headers()["last-event-id"] ?? "");
      const frames = cursors.length === 1 ? [retrieved] : [initial, failed, retried, verified, finished];
      // Another client retried while this browser was disconnected. A historical
      // failure must not hide the newer retry/result in the same replay stream.
      // Comments, CRLF lines and an old event also check framing/deduplication.
      const body = ": fixture heartbeat\r\n\r\n" + frames.map((job) =>
        `id: ${job.last_event_id}\r\nevent: progress\r\ndata: ${JSON.stringify(job)}\r\n\r\n`
      ).join("");
      await route.fulfill({ contentType: "text/event-stream", body });
    } else {
      await route.fulfill({ json: initial });
    }
  });
  await page.goto("/recommend");
  await expect(page.getByRole("region", { name: "推荐结果" })).toBeVisible();
  await expect(page.getByRole("region", { name: "推荐任务进度" })).toContainText("已完成");
  await expect(page.getByRole("region", { name: "推荐任务进度" })).toContainText("第 2 次执行");
  expect(cursors).toEqual(["2", "3"]);
});

test("UI fixture: an interrupted submission survives a rate-limited replay and retains its original credentials", async ({ page }) => {
  const query = "E2E 提交响应中断后的帝国推荐";
  const job = complete(fixture(query));
  const submissions: { key: string; token: string; body: string | null }[] = [];
  await page.route(`**${root}**`, async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === root) {
      submissions.push({
        key: route.request().headers()["idempotency-key"],
        token: route.request().headers()["x-recommendation-token"],
        body: route.request().postData(),
      });
      if (submissions.length === 1) await route.abort("failed");
      else if (submissions.length === 2) await route.fulfill({
        status: 429, headers: { "Retry-After": "1" }, json: { detail: "E2E fixture: admission replay is temporarily rate limited." },
      });
      else await route.fulfill({ status: 202, json: job });
    } else if (path.endsWith("/events")) {
      await route.fulfill({ contentType: "text/event-stream", body: `id: ${job.last_event_id}\nevent: progress\ndata: ${JSON.stringify(job)}\n\n` });
    } else {
      await route.fulfill({ json: job });
    }
  });
  await page.goto("/recommend");
  await page.getByLabel("你的战役构想").fill(query);
  await page.getByRole("button", { name: "寻找适合的模组", exact: true }).click();
  await expect(page.getByRole("button", { name: "恢复提交", exact: true })).toBeVisible();
  expect(submissions).toHaveLength(1);
  await page.reload();
  await expect(page.getByRole("button", { name: "恢复提交", exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "推荐任务进度" })).toContainText("提交结果待确认");
  await expect(page.getByRole("region", { name: "推荐任务进度" })).not.toContainText("任务已保存");
  expect(submissions).toHaveLength(2);
  await page.getByRole("button", { name: "恢复提交", exact: true }).click();
  await expect(page.getByRole("region", { name: "推荐结果" })).toBeVisible();
  expect(submissions).toHaveLength(3);
  expect(submissions[1]).toEqual(submissions[0]);
  expect(submissions[2]).toEqual(submissions[0]);
  expect(submissions[0].key).toMatch(/^[A-Za-z0-9_-]{32,128}$/);
  expect(submissions[0].token).toMatch(/^[A-Za-z0-9_-]{32,128}$/);
});
