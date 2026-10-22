"use client";

import { formatDate } from "@/lib/api";
import type { Health, ProviderStatus as Status } from "@/lib/types";
import { Icon } from "./icons";

function stateLabel(provider: Status | undefined, configured: boolean): string {
  if (!provider) return configured ? "已配置，尚无调用记录" : "尚未配置";
  if (provider.configuration === "not_configured") return "尚未配置";
  if (provider.configuration === "incomplete") return "配置尚未完整";
  if (provider.configuration === "invalid") return "配置需要检查";
  if (!provider.calls_enabled) return "调用已暂停";
  if (!provider.last_call) return "已配置，尚无调用记录";
  const labels = {
    started: "最近调用尚无完成回执",
    succeeded: "最近调用成功",
    failed: "最近调用未成功",
    cancelled: "最近调用已中断",
  };
  return labels[provider.last_call.status];
}

export function recommendationMode(health: Health): string {
  const model = health.providers?.llm;
  if (!health.llm_configured) return "规则理解已就绪";
  if (model && !model.calls_enabled) return "模型调用已暂停";
  if (model?.last_call?.status === "succeeded") return "最近模型调用成功";
  if (model?.last_call?.status === "failed" || model?.last_call?.status === "cancelled") return "模型可回退至规则";
  return "模型已配置 · 待核验";
}

export function ProviderStatus({ health, onRefresh }: { health: Health; onRefresh: () => void }) {
  const llm = health.providers?.llm;
  const embedding = health.providers?.embedding;
  const observed = [llm?.last_call?.completed_at, llm?.last_call?.at, embedding?.last_call?.completed_at, embedding?.last_call?.at]
    .filter((value): value is string => Boolean(value)).sort().at(-1);

  return <details className="provider-status" aria-label="参谋服务状态">
    <summary><span><Icon name="book"/><strong>参谋服务状态</strong></span><span>查看理解与检索方式<Icon name="chevron"/></span></summary>
    <div className="provider-status-content">
      <dl>
        <div><dt>需求理解</dt><dd>{stateLabel(llm, health.llm_configured)}</dd><p>{health.llm_configured && llm?.calls_enabled !== false ? "尝试模型理解；未成功时保留规则解析。" : "按规则识别派系、内容偏好与排除项。"}</p></div>
        <div><dt>内容匹配</dt><dd>{health.retrieval_mode === "hybrid" ? "尝试语义与关键词匹配" : "关键词匹配"}</dd><p>{health.retrieval_mode === "hybrid" ? `语义服务：${stateLabel(embedding, Boolean(health.embedding_configured))}。索引不足或调用失败时使用关键词。` : "根据已收录的标题、标签与作者说明检索。"}</p></div>
      </dl>
      <p className="provider-status-note">配置和最近调用记录仅作参考。本次实际使用的理解方式、检索方式与回退原因，会随推荐结果展示。</p>
      <div className="provider-status-footer"><span>{observed ? `最近调用记录：${formatDate(observed, true)}（北京时间）` : "尚无外部模型调用记录"}</span><button type="button" className="text-link" onClick={onRefresh}><Icon name="refresh"/>刷新状态</button></div>
    </div>
  </details>;
}
