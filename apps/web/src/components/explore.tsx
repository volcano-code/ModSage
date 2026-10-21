"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { useRemote } from "@/lib/use-remote";
import type { ModPage } from "@/lib/types";
import { Icon, Mark } from "./icons";
import { ModCard } from "./mod-card";
import { EmptyState, ErrorNotice, LoadingCards } from "./ui";

const categories = [
  ["", "全部内容"], ["units", "兵种扩展"], ["campaign", "战役玩法"], ["visual", "视觉优化"],
  ["ui", "界面与体验"], ["battle", "战斗调整"], ["overhaul", "大型重做"], ["translation", "语言与汉化"], ["other", "其他内容"],
];
const sorts = [["updated", "最近更新"], ["newest", "最新发布"], ["popular", "当前订阅数"], ["relevant", "相关程度"]];

export function Explore() {
  const router = useRouter();
  const params = useSearchParams();
  const query = params.get("q") ?? "";
  const category = categories.some(([key]) => key === params.get("category")) ? params.get("category") ?? "" : "";
  const sort = sorts.some(([key]) => key === params.get("sort")) ? params.get("sort") ?? "updated" : "updated";
  const page = Math.min(10000, Math.max(1, Math.floor(Number(params.get("page"))) || 1));
  const [draft, setDraft] = useState(query);
  const search = new URLSearchParams({ q: query, category, sort, page: String(page), page_size: "12" });
  const result = useRemote<ModPage>(`/mods?${search}`);

  function navigate(patch: Record<string, string>) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(patch)) { if (value) next.set(key, value); else next.delete(key); }
    router.push(`/explore?${next.toString()}`, { scroll: false });
  }

  return <>
    <div className="page-heading"><div><span className="eyebrow">THE GREAT ARCHIVE · 模组档案</span><h1>翻开旧世界的模组档案。</h1><p>从兵种到战役，从细节到重塑。循着你的兴趣，找到值得带上战场的改动。</p></div><Mark className="page-crest"/></div>
    <form className="search-form" onSubmit={(event) => { event.preventDefault(); navigate({ q: draft.trim(), page: "1" }); }}><Icon name="search"/><input aria-label="搜索模组" value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="搜索名称、派系、关键词……" maxLength={300}/><button className="button button-gold" type="submit">搜索<Icon name="arrow"/></button></form>
    <div className="explore-layout">
      <aside className="filter-panel"><div className="filter-banner"><Icon name="scroll"/><span className="eyebrow">ARCHIVE INDEX</span><h2>模组分类</h2></div><div className="category-list">{categories.map(([value, label]) => <button key={value} type="button" aria-pressed={category === value} className={category === value ? "selected" : ""} onClick={() => navigate({ category: value, page: "1" })}>{label}<Icon name="chevron"/></button>)}</div><div className="filter-note"><Icon name="shield"/><strong>出征前，请先核对</strong><p>中文介绍不等于游戏内汉化。最近更新也不代表已确认版本兼容。</p></div></aside>
      <section className="results-panel" aria-label="模组搜索结果"><div className="results-toolbar"><p>{result.loading ? "正在查询目录…" : result.error ? "目录暂不可用" : <><strong>{result.data?.total ?? 0}</strong> 个收录结果</>}{query && <span className="query-label">“{query}”</span>}</p><label className="sort-control">排序<select aria-label="排序方式" value={sort} onChange={(event) => navigate({ sort: event.target.value, page: "1" })}>{sorts.map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label></div>
        {sort === "popular" && <p className="source-note">按来源可取得的当前订阅数排序；缺失数据不作热门判断。</p>}
        {result.loading ? <LoadingCards count={6}/> : result.error ? <ErrorNotice message={result.error} onRetry={result.reload}/> : result.data?.items.length ? <div className="mod-grid explore-grid">{result.data.items.map((mod) => <ModCard mod={mod} key={mod.id}/>)}</div> : <EmptyState icon="search" title={query || category ? "暂时没有匹配的收录内容" : "目录还没有收录内容"}><p>{query || category ? "试试更简短的关键词，或调整分类。也可以用一句话描述需求，让参谋帮你寻找。" : "首次同步完成后，真实的 Workshop 内容将出现在这里。"}</p>{query || category ? <button type="button" className="text-link" onClick={() => { setDraft(""); router.push("/explore"); }}>清除筛选条件<Icon name="refresh"/></button> : null}</EmptyState>}
        {result.data && result.data.total > result.data.page_size && <nav className="pagination" aria-label="目录分页"><button className="button button-quiet" disabled={page <= 1} onClick={() => navigate({ page: String(page - 1) })}>上一页</button><span>第 {page} / {Math.ceil(result.data.total / result.data.page_size)} 页</span><button className="button button-quiet" disabled={page * result.data.page_size >= result.data.total} onClick={() => navigate({ page: String(page + 1) })}>下一页</button></nav>}
      </section>
    </div>
  </>;
}
