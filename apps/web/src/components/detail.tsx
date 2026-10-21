"use client";

import Link from "next/link";
import { categoryName, count, formatDate, safeSourceUrl, workshopUrl } from "@/lib/api";
import { useRemote } from "@/lib/use-remote";
import type { Mod } from "@/lib/types";
import { Icon } from "./icons";
import { ModArtwork, SaveButton } from "./mod-card";
import { ErrorNotice } from "./ui";

const evidenceLabels: Record<string, string> = { description: "作者说明", summary_zh: "中文解读依据", title: "作品名称", tags: "作者标签", category: "内容分类", chinese_support: "中文支持", compatibility_status: "兼容性声明", dependencies: "前置要求", subscribers: "订阅记录", favorites: "收藏记录", updated_at: "作者更新时间", published_at: "作品发布时间", source: "来源记录" };

const relationLabels: Record<string, string> = { requires: "必需前置", translation_of: "汉化对应本体", patch_for: "补丁对应内容", conflicts_with: "已知冲突声明", load_before: "作者要求先加载", load_after: "作者要求后加载", collection_contains: "合集成员" };

export function Detail({ id }: { id: string }) {
  const mod = useRemote<Mod>(`/mods/${id}`);
  const item = mod.data;
  return <>
    <nav className="breadcrumbs" aria-label="面包屑"><Link href="/explore">探索模组</Link><Icon name="chevron"/><span>{item ? categoryName(item.category) : "模组详情"}</span></nav>
    {mod.loading ? <div className="detail-skeleton" aria-busy="true"><div className="skeleton skeleton-line"/><div className="skeleton skeleton-image"/><div className="skeleton skeleton-line"/></div> : mod.error ? <ErrorNotice message={mod.error} onRetry={mod.reload}/> : item ? <>
      <header className="detail-heading"><div><span className="eyebrow">ARCHIVE / {item.id}</span><h1>{item.title}</h1><div className="detail-byline"><span>{item.author ? `作者 · ${item.author}` : "作者信息待确认"}</span><span>更新于 {formatDate(item.updated_at)}</span></div></div><SaveButton mod={item} expanded/></header>
      <div className="detail-layout"><div className="detail-content"><ModArtwork mod={item} large/><div className="detail-tags tag-row">{item.tags.map((tag) => <span className="tag" key={tag}>{tag}</span>)}</div>
        <section className="detail-section"><div className="section-heading"><h2>内容解读</h2><span className="mode-badge">{item.summary_status === "generated" ? "中文生成介绍" : item.summary_status === "source_chinese" ? "作者中文说明" : "介绍待生成"}</span></div>{item.summary_zh ? <p className="long-copy">{item.summary_zh}</p> : <div className="notice"><Icon name="book"/><p>这条 Mod 的中文介绍尚未生成。你可以阅读下方作者原文，或访问 Workshop。</p></div>}
          <details className="source-details" open={!item.summary_zh}><summary>作者原文摘录<Icon name="chevron"/></summary><p className="long-copy source-copy">{item.description_excerpt || "来源尚未提供可读取的说明。"}</p></details>
        </section>
        <section className="detail-section"><h2>前置与配套关系</h2>{item.relations.length ? <div className="relation-list">{item.relations.map((relation, index) => <div key={`${relation.kind}-${relation.target_id}-${index}`}><span><Icon name="layers"/>{relationLabels[relation.kind] ?? relation.kind}</span><Link href={`/mods/${relation.target_id}`}>Workshop #{relation.target_id}<Icon name="arrow"/></Link>{workshopUrl(relation.target_id) && <a href={workshopUrl(relation.target_id)} target="_blank" rel="noopener noreferrer" aria-label={`在 Workshop 查看 ${relation.target_id}`}><Icon name="external"/></a>}</div>)}</div> : <p className="muted long-copy">目前没有收录到明确的关系证据。没有记录不代表无需前置，也不代表与其他 Mod 兼容。</p>}</section>
        <section className="detail-section"><h2>事实依据</h2><p className="section-description">保留来源片段与采集时间，便于你判断信息是否适用。</p>{item.evidence.length ? <div className="evidence-list">{item.evidence.map((evidence) => <details key={evidence.id}><summary><span><Icon name="book"/>{evidenceLabels[evidence.field] ?? evidence.field}</span><span>{formatDate(evidence.observed_at)}<Icon name="chevron"/></span></summary><blockquote>{evidence.excerpt || "此事实来自来源结构化字段。"}</blockquote>{evidence.value !== null && evidence.value !== undefined && <p className="evidence-value">记录值：{typeof evidence.value === "string" ? evidence.value : JSON.stringify(evidence.value)}</p>}{safeSourceUrl(evidence.source_url) && <a className="text-link" href={safeSourceUrl(evidence.source_url)} target="_blank" rel="noopener noreferrer">查看原始来源<Icon name="external"/></a>}</details>)}</div> : <p className="muted">暂无单独的事实摘录，可通过右侧原始页面核对信息。</p>}</section>
      </div><aside className="detail-aside"><div className="aside-panel"><span className="eyebrow">THE QUARTERMASTER’S NOTES</span><h2>军需官的备忘</h2><dl className="fact-list"><div><dt>前置关系采集</dt><dd>{item.dependencies_known ? "已取得来源声明" : "尚未取得完整记录"}</dd></div><div><dt>游戏内中文</dt><dd>{item.chinese_support === "unknown" ? "尚未确认" : item.chinese_support === "translation_available" ? "有配套汉化记录" : "作者声明支持"}</dd></div><div><dt>版本兼容性</dt><dd>{item.compatibility_status === "unknown" ? "尚未确认" : "有作者声明，需核对适用范围"}</dd></div><div><dt>当前订阅</dt><dd>{count(item.subscribers)}</dd></div><div><dt>当前收藏</dt><dd>{count(item.favorites)}</dd></div><div><dt>首次发布</dt><dd>{formatDate(item.published_at)}</dd></div><div><dt>数据采集</dt><dd>{formatDate(item.fetched_at, true)}</dd></div></dl><p className="aside-note"><Icon name="alert"/>中文解读不等于游戏内汉化；来源更新不等于已通过兼容测试。</p>{workshopUrl(item.id) && <a className="button button-gold full-width" href={workshopUrl(item.id)} target="_blank" rel="noopener noreferrer">在 Steam Workshop 查看<Icon name="external"/></a>}<Link className="button button-quiet full-width" href="/collection">查看我的清单<Icon name="arrow"/></Link></div><div className="source-card"><Icon name="shield"/><div><strong>创作属于作者</strong><p>订阅、支持与反馈，请前往作者的原始页面。</p></div></div></aside></div>
    </> : null}
  </>;
}
