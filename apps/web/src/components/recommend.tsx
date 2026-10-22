"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRemote } from "@/lib/use-remote";
import { useRecommendationJob } from "@/lib/use-recommendation-job";
import type { Health, Recommendation } from "@/lib/types";
import { Icon, Mark } from "./icons";
import { ModCard } from "./mod-card";
import { CampaignBanner } from "./game-media";
import { EmptyState, ErrorNotice } from "./ui";
import { RecommendationHistory, RecommendationJobPanel } from "./recommendation-job-panel";
import { ProviderStatus, recommendationMode } from "./provider-status";

const examples = ["想玩震旦，增加新兵种，不要大型重做", "找一些改善界面体验的 Mod，最好有中文", "想让帝国战役更有变化，尽量保持原版体验"];

const constraintLabels: Record<string, string> = { faction: "派系", factions: "派系", excluded_factions: "排除派系", categories: "内容类型", excluded_categories: "排除类型", exclude_categories: "排除类型", required_topics: "内容要求", excluded_tags: "排除内容", keywords: "关键词", query: "需求", chinese_required: "必须有中文依据", require_chinese: "必须有中文依据", chinese_preferred: "偏好中文", exclude_overhaul: "排除大修", limit: "推荐数量", chinese_support: "中文要求", language: "语言", max_total_mods: "清单数量上限" };

const factionLabels: Record<string, string> = {
  empire: "帝国", cathay: "震旦", dwarfs: "矮人", kislev: "基斯里夫", skaven: "鼠人",
  high_elves: "高等精灵", dark_elves: "黑暗精灵", lizardmen: "蜥蜴人", greenskins: "绿皮",
  chaos: "混沌", bretonnia: "巴托尼亚", vampires: "吸血鬼",
};
const contentLabels: Record<string, string> = {
  artillery: "火炮", units: "兵种", overhaul: "大修", cheat: "作弊", campaign: "战役",
  battle: "战斗", ui: "界面与体验", visual: "视觉优化", translation: "汉化", other: "其他内容",
};
const enumLabels: Record<string, Record<string, string>> = {
  faction: factionLabels, factions: factionLabels, excluded_factions: factionLabels,
  categories: contentLabels, excluded_categories: contentLabels, exclude_categories: contentLabels,
  required_topics: contentLabels,
};

function constraintValue(value: unknown, field: string): string {
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) return value.map((item) => constraintValue(item, field)).join("、");
  if (typeof value === "object" && value !== null) return Object.entries(value).map(([key, item]) => `${constraintLabels[key] ?? key}: ${constraintValue(item, key)}`).join("，");
  if (typeof value === "string") return enumLabels[field]?.[value] ?? value;
  return String(value ?? "未指定");
}

function MatchingEvidence({ item }: { item: Recommendation }) {
  if (!item.retrieval) return null;
  return <details className="matching-evidence">
    <summary><Icon name="search"/>查看匹配依据<Icon name="chevron"/></summary>
    <div>
      <div className="matching-channels">{item.retrieval.channels.includes("lexical") && <span>关键词匹配</span>}{item.retrieval.channels.includes("semantic") && <span>语义相关</span>}</div>
      {item.retrieval.matched_terms.length > 0 ? <><p>在标题、标签或作者说明中匹配到：</p><div className="matched-terms">{item.retrieval.matched_terms.map((term) => <span key={term}>{term}</span>)}</div></> : <p>{item.retrieval.channels.includes("semantic") ? "通过内容语义相关性召回，没有直接命中的关键词。" : "排序依据来自已收录的标题、标签与作者说明。"}</p>}
      <p className="matching-note">请结合作者原文和下方注意事项判断是否适合你的战役。</p>
    </div>
  </details>;
}

export function Recommend() {
  const health = useRemote<Health>("/health");
  const task = useRecommendationJob();
  const [query, setQuery] = useState("");
  const inputQuery = task.selected?.query ?? query;
  const submittedQuery = task.job?.query ?? task.selected?.query ?? "";
  const result = task.job?.result ?? null;
  const busy = Boolean(task.operation) || task.job?.status === "queued" || task.job?.status === "running";
  const unconfirmed = Boolean(task.selected && !task.selected.id);
  const taskId = task.job?.id;
  const taskStatus = task.job?.status;
  const refreshHealth = health.reload;

  useEffect(() => {
    if (taskStatus === "completed" || taskStatus === "failed" || taskStatus === "cancelled") refreshHealth();
  }, [taskId, taskStatus, refreshHealth]);

  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!busy && !unconfirmed && task.ready) task.submit(inputQuery);
  }

  function editQuery(value: string) {
    if (task.selected) task.newDraft();
    setQuery(value);
  }

  function newDraft() {
    setQuery(inputQuery);
    task.newDraft();
  }

  return <>
    <CampaignBanner scene="khorne" eyebrow="THE WAR COUNCIL · 战役参谋" title="下一场远征，从你的构想开始。" description="告诉参谋你想玩的派系、期待的改变，以及需要避开的内容。"/>
    <div className="recommend-layout"><aside className="recommend-guide"><div className="guide-symbol"><Mark/></div><span className="eyebrow">A LETTER TO YOUR SAGE</span><h2>落笔之前</h2><p>告诉我们你想玩什么，以及不想要什么。</p><ol><li><span>01</span><div><strong>想玩谁？</strong><p>例如帝国、震旦，或你偏爱的派系。</p></div></li><li><span>02</span><div><strong>想改变什么？</strong><p>兵种、战役、界面，或整体体验。</p></div></li><li><span>03</span><div><strong>有什么取舍？</strong><p>不要大修、必须有汉化，或尽量轻量。</p></div></li></ol><div className="guide-bottom"><Icon name="shield"/><p>没有证据的信息会保持未知。推荐不代表已完成游戏内兼容测试。</p></div></aside>
      <section className="recommend-main"><form className="prompt-panel" onSubmit={submit}><div className="order-heading"><span>CAMPAIGN ORDERS</span><Icon name="swords"/><span>致我的战役参谋</span></div><div className="prompt-topline"><label htmlFor="request-description">你的战役构想</label><span className="mode-badge">{health.data ? recommendationMode(health.data) : health.error ? "服务暂不可用" : "正在检查服务"}</span></div><textarea id="request-description" value={inputQuery} onChange={(event) => editQuery(event.target.value)} maxLength={1000} minLength={2} required placeholder="例如：我主要玩帝国，想增加火炮和兵种内容，不要大型重做，最好有中文。" disabled={busy || unconfirmed || !task.ready}/><div className="prompt-footer"><span>{inputQuery.length} / 1000<span className="prompt-hint"> · 尽量写清偏好和排除项</span></span><div><button type="submit" className="button button-gold" disabled={busy || unconfirmed || !task.ready || inputQuery.trim().length < 2}><Icon name={busy ? "refresh" : "spark"} className={busy ? "spinning" : ""}/>{busy ? task.operation === "submit" ? "正在提交任务" : "推荐任务处理中" : "寻找适合的模组"}{!busy && <Icon name="arrow"/>}</button></div></div></form>
      {task.selected && <RecommendationJobPanel record={task.selected} job={task.job} connection={task.connection} operation={task.operation} hasError={Boolean(task.error)} onCancel={task.cancel} onRetry={task.retry} onReconnect={task.reconnect} onNew={newDraft}/>}
      <div className="example-prompts"><span>试试这样问</span>{examples.map((example) => <button key={example} disabled={busy || unconfirmed} onClick={() => editQuery(example)}>{example}<Icon name="arrow"/></button>)}</div>
      {health.data && <ProviderStatus health={health.data} onRefresh={health.reload}/> }
      {health.error && <ErrorNotice message={health.error} onRetry={health.reload}/>}
      {task.error && <ErrorNotice message={task.error}/>}
      {task.storageWarning && <div className="notice" role="status"><Icon name="alert"/><p>{task.storageWarning}</p></div>}
      {!busy && result && <section className="recommend-results" aria-label="推荐结果"><div className="results-title"><div><span className="eyebrow">YOUR RECOMMENDATIONS</span><h2>{result.status === "failed" ? "这次推荐未能完成" : result.items.length ? `为你找到 ${result.items.length} 个候选` : "暂未找到满足条件的候选"}</h2></div><span className="mode-badge">{result.status === "failed" ? "任务未完成" : result.retrieval?.method === "hybrid_rrf" ? "语义与关键词匹配" : "关键词匹配"}</span></div><p className="submitted-query">“{submittedQuery}”</p>
        {result.retrieval && <details className="retrieval-summary"><summary><Icon name="compass"/>{result.retrieval.method === "hybrid_rrf" ? "结合关键词与语义寻找候选" : "根据标题、标签与作者说明寻找候选"}<Icon name="chevron"/></summary><div><p>先检查派系、排除项与中文要求，再对符合条件的目录内容排序。</p><dl><div><dt>需求理解</dt><dd>{result.engine === "llm" ? "模型辅助" : "规则解析"}</dd></div><div><dt>符合已知条件</dt><dd>{result.retrieval.eligible_count} 项</dd></div><div><dt>检索候选</dt><dd>{result.retrieval.candidate_count} 项</dd></div></dl></div></details>}
        {Object.keys(result.constraints ?? {}).length > 0 && <div className="constraint-list">{Object.entries(result.constraints).filter(([, value]) => value !== null && value !== "" && (!Array.isArray(value) || value.length > 0)).map(([key, value]) => <span key={key}><b>{constraintLabels[key] ?? key}</b>{constraintValue(value, key)}</span>)}</div>}
        {result.warnings.length > 0 && <div className="notice"><Icon name="alert"/><div>{result.warnings.map((warning, i) => <p key={i}>{warning}</p>)}</div></div>}
        {result.items.length > 0 ? <div className="recommend-list">{result.items.map((item, index) => <article className="recommend-item" key={item.mod.id}><div className="recommend-item-top"><span>推荐候选 <b>{String(index + 1).padStart(2, "0")}</b></span><Link href={`/mods/${item.mod.id}`} className="text-link">查看详情与依据<Icon name="arrow"/></Link></div><div className="recommend-item-content"><ModCard mod={item.mod}/><div className="recommend-reasons"><h3><Icon name="check"/>为什么值得考虑</h3><ul>{item.reasons.map((reason, i) => <li key={i}>{reason}</li>)}</ul><MatchingEvidence item={item}/>{item.caveats.length > 0 && <><h3 className="caveat-heading"><Icon name="alert"/>需要留意</h3><ul>{item.caveats.map((caveat, i) => <li key={i}>{caveat}</li>)}</ul></>}<p className="recommend-source">查看作者说明后，再决定是否加入自己的游戏。</p></div></div></article>)}</div> : <EmptyState icon="search" title={result.status === "failed" ? "请稍后重试" : "保持条件，或者试试调整范围"}><p>{result.status === "failed" ? "本次请求没有得到完整结果，可以保留当前条件重新尝试。" : "目录覆盖与事实信息可能有限。你可以减少一项软偏好，再次搜索。"}</p></EmptyState>}
        <p className="run-note">本次记录：{result.run_id}</p>
      </section>}
      {!busy && !result && !task.error && !task.selected && <div className="recommend-placeholder"><div className="placeholder-symbol"><Icon name="compass"/></div><span className="eyebrow">YOUR NEXT CAMPAIGN AWAITS</span><h3>战役尚未开始，可能已在眼前。</h3><p>写下你的构想，候选模组与推荐理由将在这里展开。</p></div>}
      <RecommendationHistory records={task.history.items} selectedKey={task.history.selected_key} disabled={Boolean(task.operation)} onSelect={task.select} onForget={task.forget}/>
      </section>
    </div>
  </>;
}
