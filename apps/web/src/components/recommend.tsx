"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useRemote } from "@/lib/use-remote";
import type { Health, RecommendationRun } from "@/lib/types";
import { Icon, Mark } from "./icons";
import { ModCard } from "./mod-card";
import { EmptyState, ErrorNotice } from "./ui";

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

export function Recommend() {
  const health = useRemote<Health>("/health");
  const [query, setQuery] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const [result, setResult] = useState<RecommendationRun | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cancelled, setCancelled] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);
  const lastRequestRef = useRef(0);

  useEffect(() => () => controllerRef.current?.abort(), []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (query.trim().length < 2 || busy) return;
    controllerRef.current?.abort();
    const controller = new AbortController();
    const requestId = ++lastRequestRef.current;
    controllerRef.current = controller;
    setBusy(true); setError(null); setResult(null); setCancelled(false); setSubmittedQuery(query.trim());
    try {
      const response = await api<RecommendationRun>("/recommendations", { method: "POST", body: JSON.stringify({ query: query.trim(), limit: 5 }), signal: controller.signal });
      if (requestId === lastRequestRef.current && !controller.signal.aborted) setResult(response);
    } catch (failure) {
      if (requestId === lastRequestRef.current && !controller.signal.aborted) setError(failure instanceof Error ? failure.message : "推荐请求未能完成。");
    } finally {
      if (requestId === lastRequestRef.current) setBusy(false);
    }
  }

  function cancel() {
    controllerRef.current?.abort();
    ++lastRequestRef.current;
    setBusy(false); setCancelled(true);
  }

  return <>
    <div className="page-heading"><div><span className="eyebrow">THE WAR COUNCIL · 战役参谋</span><h1>下一场远征，从你的构想开始。</h1><p>告诉参谋你想玩的派系、期待的改变，以及需要避开的内容。</p></div><Mark className="page-crest"/></div>
    <div className="recommend-layout"><aside className="recommend-guide"><div className="guide-symbol"><Mark/></div><span className="eyebrow">A LETTER TO YOUR SAGE</span><h2>落笔之前</h2><p>告诉我们你想玩什么，以及不想要什么。</p><ol><li><span>01</span><div><strong>想玩谁？</strong><p>例如帝国、震旦，或你偏爱的派系。</p></div></li><li><span>02</span><div><strong>想改变什么？</strong><p>兵种、战役、界面，或整体体验。</p></div></li><li><span>03</span><div><strong>有什么取舍？</strong><p>不要大修、必须有汉化，或尽量轻量。</p></div></li></ol><div className="guide-bottom"><Icon name="shield"/><p>没有证据的信息会保持未知。推荐不代表已完成游戏内兼容测试。</p></div></aside>
      <section className="recommend-main"><form className="prompt-panel" onSubmit={submit}><div className="order-heading"><span>CAMPAIGN ORDERS</span><Icon name="swords"/><span>致我的战役参谋</span></div><div className="prompt-topline"><label htmlFor="request-description">你的战役构想</label><span className="mode-badge">{health.data ? health.data.llm_configured ? "模型辅助推荐可用" : "规则检索模式" : health.error ? "服务暂不可用" : "正在检查服务"}</span></div><textarea id="request-description" value={query} onChange={(event) => setQuery(event.target.value)} maxLength={1000} minLength={2} required placeholder="例如：我主要玩帝国，想增加火炮和兵种内容，不要大型重做，最好有中文。" disabled={busy}/><div className="prompt-footer"><span>{query.length} / 1000<span className="prompt-hint"> · 尽量写清偏好和排除项</span></span><div>{busy && <button type="button" className="button button-quiet" onClick={cancel}>停止等待</button>}<button type="submit" className="button button-gold" disabled={busy || query.trim().length < 2}><Icon name={busy ? "refresh" : "spark"} className={busy ? "spinning" : ""}/>{busy ? "正在生成推荐" : "寻找适合的模组"}{!busy && <Icon name="arrow"/>}</button></div></div></form>
      <div className="example-prompts"><span>试试这样问</span>{examples.map((example) => <button key={example} disabled={busy} onClick={() => setQuery(example)}>{example}<Icon name="arrow"/></button>)}</div>
      {health.data && !health.data.llm_configured && <p className="source-note"><Icon name="book"/>当前根据目录关键词与已知条件匹配。</p>}
      {health.error && <ErrorNotice message={health.error} onRetry={health.reload}/>}
      {error && <ErrorNotice message={error}/>}
      {cancelled && <div className="notice" role="status"><Icon name="x"/><p>已停止等待本次结果。服务端已经开始的处理可能仍会完成。</p></div>}
      {busy && <div className="recommend-progress" role="status"><span className="progress-ring"/><div><strong>正在结合你的需求检索与检查</strong><p>结果会在本次请求完成后展示。你可以停止等待。</p></div></div>}
      {!busy && result && <section className="recommend-results" aria-label="推荐结果"><div className="results-title"><div><span className="eyebrow">YOUR RECOMMENDATIONS</span><h2>{result.items.length ? `为你找到 ${result.items.length} 个候选` : "暂未找到满足条件的候选"}</h2></div><span className="mode-badge">{result.engine === "llm" ? "模型辅助" : "规则检索"}</span></div><p className="submitted-query">“{submittedQuery}”</p>
        {Object.keys(result.constraints ?? {}).length > 0 && <div className="constraint-list">{Object.entries(result.constraints).filter(([, value]) => value !== null && value !== "" && (!Array.isArray(value) || value.length > 0)).map(([key, value]) => <span key={key}><b>{constraintLabels[key] ?? key}</b>{constraintValue(value, key)}</span>)}</div>}
        {result.warnings.length > 0 && <div className="notice"><Icon name="alert"/><div>{result.warnings.map((warning, i) => <p key={i}>{warning}</p>)}</div></div>}
        {result.items.length > 0 ? <div className="recommend-list">{result.items.map((item, index) => <article className="recommend-item" key={item.mod.id}><div className="recommend-item-top"><span>推荐候选 <b>{String(index + 1).padStart(2, "0")}</b></span><Link href={`/mods/${item.mod.id}`} className="text-link">查看详情与依据<Icon name="arrow"/></Link></div><div className="recommend-item-content"><ModCard mod={item.mod}/><div className="recommend-reasons"><h3><Icon name="check"/>为什么值得考虑</h3><ul>{item.reasons.map((reason, i) => <li key={i}>{reason}</li>)}</ul>{item.caveats.length > 0 && <><h3 className="caveat-heading"><Icon name="alert"/>需要留意</h3><ul>{item.caveats.map((caveat, i) => <li key={i}>{caveat}</li>)}</ul></>}<p className="recommend-source">查看作者说明后，再决定是否加入自己的游戏。</p></div></div></article>)}</div> : <EmptyState icon="search" title="保持条件，或者试试调整范围"><p>目录覆盖与事实信息可能有限。你可以减少一项软偏好，再次搜索。</p></EmptyState>}
        <p className="run-note">本次记录：{result.run_id}</p>
      </section>}
      {!busy && !result && !error && !cancelled && <div className="recommend-placeholder"><div className="placeholder-symbol"><Icon name="compass"/></div><span className="eyebrow">YOUR NEXT CAMPAIGN AWAITS</span><h3>战役尚未开始，可能已在眼前。</h3><p>写下你的构想，候选模组与推荐理由将在这里展开。</p></div>}
      </section>
    </div>
  </>;
}
