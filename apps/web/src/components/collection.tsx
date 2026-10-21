"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api, workshopUrl } from "@/lib/api";
import { toggleSaved, useSavedMods } from "@/lib/saved";
import type { Validation } from "@/lib/types";
import { Icon, Mark } from "./icons";
import { EmptyState, ErrorNotice } from "./ui";

export function Collection() {
  const saved = useSavedMods();
  const [result, setResult] = useState<{ signature: string; data: Validation } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const signature = saved.map((mod) => mod.id).join(",");
  const currentResult = result?.signature === signature ? result.data : null;

  useEffect(() => () => controllerRef.current?.abort(), []);

  async function validate() {
    controllerRef.current?.abort();
    const controller = new AbortController();
    controllerRef.current = controller;
    setBusy(true); setError(null); setResult(null);
    try {
      const response = await api<Validation>("/modlists/validate", { method: "POST", body: JSON.stringify({ mod_ids: saved.map((mod) => mod.id) }), signal: controller.signal });
      if (!controller.signal.aborted) setResult({ signature, data: response });
    } catch (failure) {
      if (!controller.signal.aborted) setError(failure instanceof Error ? failure.message : "检查未能完成。");
    } finally { if (!controller.signal.aborted) setBusy(false); }
  }

  function download() {
    const content = ["ModSage · 我的模组清单", "此清单仅记录 Workshop 链接，不表示已经验证组合兼容性。", "", ...saved.flatMap((mod, index) => [`${index + 1}. ${mod.title}`, workshopUrl(mod.id) ?? "", ""])].join("\n");
    const url = URL.createObjectURL(new Blob([content], { type: "text/plain;charset=utf-8" }));
    const link = document.createElement("a"); link.href = url; link.download = "modsage-modlist.txt"; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
    setMessage("已导出 Workshop 链接清单。");
  }

  return <>
    <div className="page-heading"><div><span className="eyebrow">THE MUSTER ROLL · 我的清单</span><h1>集结模组，准备下一场远征。</h1><p>收集心仪的内容，核对已知前置。清单保存在当前浏览器，可随时导出 Workshop 链接。</p></div><Mark className="page-crest"/></div>
    <div className="collection-toolbar"><div><Icon name="bookmark"/><strong>{saved.length}</strong><span>个已保存模组</span><span className="mode-badge">本机清单</span></div><div><button className="button button-quiet" disabled={!saved.length} onClick={download}><Icon name="download"/>导出链接</button><button className="button button-gold" disabled={!saved.length || busy} onClick={validate}><Icon name={busy ? "refresh" : "shield"} className={busy ? "spinning" : ""}/>{busy ? "正在检查" : "检查已知前置"}</button></div></div>
    {message && <p className="source-note" role="status">{message}</p>}
    {error && <ErrorNotice message={error}/>}
    {saved.length ? <div className="collection-layout"><section className="saved-list" aria-label="保存的模组">{saved.map((mod, index) => <article className="saved-row" key={mod.id}><span className="saved-number">{String(index + 1).padStart(2, "0")}</span><div><h2><Link href={`/mods/${mod.id}`}>{mod.title}</Link></h2><span>Workshop #{mod.id}</span></div><Link className="icon-button" href={`/mods/${mod.id}`} aria-label={`查看 ${mod.title}`}><Icon name="arrow"/></Link><button className="icon-button" title="从清单移除" aria-label={`从清单移除：${mod.title}`} onClick={() => { setMessage(null); if (!toggleSaved(mod)) setError("浏览器未能保存修改，请检查存储权限。"); }}><Icon name="x"/></button></article>)}</section><aside className="validation-panel"><span className="eyebrow">BEFORE THE MARCH</span><h2>出征前 · 清单检查</h2>{busy ? <div className="validation-loading" role="status"><Icon name="refresh" className="spinning"/>正在读取已收录的关系信息…</div> : currentResult ? <><div className="validation-summary"><Icon name="layers"/><div><strong>{currentResult.missing_dependencies.length}</strong><span>项已知前置未在清单中</span></div></div>{currentResult.missing_dependencies.length > 0 && <ul className="dependency-list">{currentResult.missing_dependencies.map((dependency, index) => <li key={`${dependency.mod_id}-${dependency.required_id}-${index}`}><span>#{dependency.mod_id} 需要</span><a href={workshopUrl(dependency.required_id)} target="_blank" rel="noopener noreferrer">#{dependency.required_id}<Icon name="external"/></a></li>)}</ul>}{currentResult.known_conflicts.length > 0 && <div className="notice"><Icon name="alert"/><p>存在 {currentResult.known_conflicts.length} 项已知冲突记录，请核对各条目来源。</p></div>}<div className="notice"><Icon name="alert"/><p>{currentResult.unknown_compatibility.length} 个条目的组合兼容性尚未确认。前置检查不等于游戏运行测试。</p></div>{currentResult.warnings.map((warning, i) => <p className="validation-warning" key={i}>{warning}</p>)}</> : <><div className="validation-empty"><Icon name="shield"/><p>{result ? "清单已变化，请重新检查。" : "准备好后，点击“检查已知前置”。"}</p></div><p className="muted">检查依据来自已收录的作者说明与关系记录。未发现冲突，不等于已验证兼容。</p></>}</aside></div> : <EmptyState icon="bookmark" title="你的下一场战役，还在酝酿中" href="/explore" action="去探索模组"><p>在模组卡片或详情页点击书签，将感兴趣的内容放进清单。</p></EmptyState>}
  </>;
}
