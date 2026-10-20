export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message);
    this.name = "ApiError";
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = body?.message ?? body?.detail;
    const fallback = response.status === 404 ? "没有找到这条记录。" : "服务暂时未能完成请求，请稍后重试。";
    throw new ApiError(typeof detail === "string" ? detail.slice(0, 280) : fallback, response.status);
  }
  return response.json() as Promise<T>;
}

export function formatDate(value?: string | null, includeTime = false): string {
  if (!value) return "尚未确认";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "尚未确认";
  return new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    month: "2-digit",
    day: "2-digit",
    year: "numeric",
    ...(includeTime ? { hour: "2-digit", minute: "2-digit", hour12: false } : {}),
  }).format(date);
}

export function count(value?: number | null): string {
  if (value === null || value === undefined) return "未知";
  return new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

export function workshopUrl(id: string): string | undefined {
  return /^\d{1,20}$/.test(id) ? `https://steamcommunity.com/sharedfiles/filedetails/?id=${id}` : undefined;
}

export function safeSourceUrl(value: string): string | undefined {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password || url.port) return undefined;
    if (url.hostname !== "steamcommunity.com" && url.hostname !== "community.creative-assembly.com") return undefined;
    return url.href;
  } catch { return undefined; }
}

export function safePreviewUrl(value: string | null): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password || url.port) return undefined;
    if (!["steamstatic.com", "steamusercontent.com"].some((host) => url.hostname === host || url.hostname.endsWith(`.${host}`))) return undefined;
    return url.href;
  } catch { return undefined; }
}

export const categoryNames: Record<string, string> = {
  overhaul: "大型重做", major_overhaul: "大型重做", units: "兵种扩展", visual: "视觉优化",
  graphics: "视觉优化", campaign: "战役玩法", quality_of_life: "体验优化", qol: "体验优化", ui: "界面与体验",
  translation: "语言与汉化", localization: "语言与汉化", battle: "战斗调整", other: "其他内容",
};

export function categoryName(value: string): string { return categoryNames[value] ?? value ?? "未分类"; }
