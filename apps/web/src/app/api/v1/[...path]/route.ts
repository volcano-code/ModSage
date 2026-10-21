import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const jobId = "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}";
const jobPath = new RegExp(`^recommendation-jobs/${jobId}$`);
const eventPath = new RegExp(`^recommendation-jobs/${jobId}/events$`);
const getPaths = [
  /^health$/, /^stats$/, /^mods$/, /^mods\/\d{1,20}$/, /^digests\/latest$/, /^sync\/status$/,
  /^runs\/[a-zA-Z0-9_-]{1,100}$/,
  jobPath, eventPath,
];
const postPaths = [
  /^recommendations$/, /^modlists\/validate$/, /^recommendation-jobs$/,
  new RegExp(`^recommendation-jobs/${jobId}/(?:cancel|retry)$`),
];

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const route = path.join("/");
  const allowed = (request.method === "GET" ? getPaths : postPaths).some((rule) => rule.test(route));
  if (!allowed) return NextResponse.json({ message: "此接口不对外开放。" }, { status: 404 });
  const taskRequest = route === "recommendation-jobs" || route.startsWith("recommendation-jobs/");
  const eventRequest = request.method === "GET" && eventPath.test(route);

  // Only task-specific capability/replay headers may pass through. Credentials
  // never travel in a URL and cannot reach unrelated or administrative routes.
  const headers = new Headers({ Accept: eventRequest ? "text/event-stream" : "application/json" });
  if (taskRequest) {
    for (const name of ["X-Recommendation-Token", "Idempotency-Key", "Last-Event-ID"]) {
      const value = request.headers.get(name);
      if (value !== null) {
        if (value.length > 128) {
          return NextResponse.json({ message: "任务请求参数无效。" }, { status: 400 });
        }
        headers.set(name, value);
      }
    }
  }

  let upstream: URL;
  try {
    const base = new URL(process.env.API_BASE_URL ?? "http://127.0.0.1:8000");
    if (!["http:", "https:"].includes(base.protocol) || base.username || base.password) throw new Error("invalid API base");
    upstream = new URL(`/api/v1/${route}`, base);
    for (const [key, value] of request.nextUrl.searchParams.entries()) {
      if ((!taskRequest && ["q", "category", "sort", "page", "page_size"].includes(key)) || (eventRequest && key === "after")) {
        upstream.searchParams.set(key, value);
      }
    }
  } catch {
    return NextResponse.json({ message: "服务连接配置有误。" }, { status: 503 });
  }

  let body: string | undefined;
  if (request.method === "POST") {
    if (!request.headers.get("content-type")?.includes("application/json")) {
      return NextResponse.json({ message: "请使用 JSON 请求。" }, { status: 415 });
    }
    // Read as a stream so an untrusted chunked request cannot allocate unbounded memory.
    const reader = request.body?.getReader();
    const decoder = new TextDecoder();
    let size = 0;
    body = "";
    if (reader) {
      try {
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          size += value.byteLength;
          if (size > 16_384) {
            await reader.cancel();
            return NextResponse.json({ message: "请求内容过长。" }, { status: 413 });
          }
          body += decoder.decode(value, { stream: true });
        }
        body += decoder.decode();
        JSON.parse(body);
      } catch {
        return NextResponse.json({ message: "请求内容不是有效 JSON。" }, { status: 400 });
      }
    }
  }

  try {
    if (body !== undefined) headers.set("Content-Type", "application/json");
    const response = await fetch(upstream, {
      method: request.method,
      headers,
      body,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.any([AbortSignal.timeout(40_000), request.signal]),
    });
    if (eventRequest && response.ok && response.headers.get("content-type")?.includes("text/event-stream")) {
      // Forward the readable stream immediately; buffering it would hide live
      // stages and break disconnect/replay behavior. Upstream ends before 40s.
      return new Response(response.body, {
        status: response.status,
        headers: {
          "Content-Type": "text/event-stream; charset=utf-8",
          "Cache-Control": "no-store, no-transform",
          "X-Accel-Buffering": "no",
          "X-Content-Type-Options": "nosniff",
        },
      });
    }
    if (!response.headers.get("content-type")?.includes("application/json")) {
      await response.body?.cancel();
      return NextResponse.json({ message: "数据服务返回了无法读取的内容。" }, { status: 502 });
    }
    const responseHeaders = new Headers({ "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" });
    const retryAfter = response.headers.get("retry-after");
    if (retryAfter && /^\d{1,6}$/.test(retryAfter)) responseHeaders.set("Retry-After", retryAfter);
    return new Response(response.body, {
      status: response.status,
      headers: responseHeaders,
    });
  } catch {
    return NextResponse.json({ message: "暂时无法连接数据服务，请稍后重试。" }, { status: 503 });
  }
}

export { proxy as GET, proxy as POST };
