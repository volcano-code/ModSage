import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const getPaths = [
  /^health$/, /^stats$/, /^mods$/, /^mods\/\d{1,20}$/, /^digests\/latest$/, /^sync\/status$/,
  /^runs\/[a-zA-Z0-9_-]{1,100}$/,
];
const postPaths = [/^recommendations$/, /^modlists\/validate$/];

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const route = path.join("/");
  const allowed = (request.method === "GET" ? getPaths : postPaths).some((rule) => rule.test(route));
  if (!allowed) return NextResponse.json({ message: "此接口不对外开放。" }, { status: 404 });

  let upstream: URL;
  try {
    const base = new URL(process.env.API_BASE_URL ?? "http://127.0.0.1:8000");
    if (!["http:", "https:"].includes(base.protocol) || base.username || base.password) throw new Error("invalid API base");
    upstream = new URL(`/api/v1/${route}`, base);
    for (const [key, value] of request.nextUrl.searchParams.entries()) {
      if (["q", "category", "sort", "page", "page_size"].includes(key)) upstream.searchParams.set(key, value);
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
    const response = await fetch(upstream, {
      method: request.method,
      headers: { Accept: "application/json", ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
      body,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.any([AbortSignal.timeout(40_000), request.signal]),
    });
    if (!response.headers.get("content-type")?.includes("application/json")) {
      return NextResponse.json({ message: "数据服务返回了无法读取的内容。" }, { status: 502 });
    }
    return new Response(response.body, {
      status: response.status,
      headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff" },
    });
  } catch {
    return NextResponse.json({ message: "暂时无法连接数据服务，请稍后重试。" }, { status: 503 });
  }
}

export { proxy as GET, proxy as POST };
