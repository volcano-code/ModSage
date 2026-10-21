"use client";

import Image from "next/image";
import Link from "next/link";
import { useState } from "react";
import { categoryName, count, formatDate, safePreviewUrl } from "@/lib/api";
import { toggleSaved, useSavedMods } from "@/lib/saved";
import type { Mod } from "@/lib/types";
import { Icon, Mark } from "./icons";

export function SaveButton({ mod, expanded = false }: { mod: Pick<Mod, "id" | "title">; expanded?: boolean }) {
  const saved = useSavedMods();
  const selected = saved.some((item) => item.id === mod.id);
  const [error, setError] = useState(false);
  return <><button type="button" className={expanded ? `button ${selected ? "button-quiet" : "button-gold"}` : `save-button ${selected ? "saved" : ""}`} aria-label={`${selected ? "从清单移除" : "加入清单"}：${mod.title}`} aria-pressed={selected} title={selected ? "从清单移除" : "加入清单"} onClick={() => setError(!toggleSaved(mod))}><Icon name={selected ? "check" : "bookmark"}/>{expanded && (selected ? "已加入清单" : "加入我的清单")}</button>{error && <span role="alert" className="inline-error">无法保存：请检查浏览器存储权限，清单最多 100 项。</span>}</>;
}

export function ModArtwork({ mod, large = false }: { mod: Mod; large?: boolean }) {
  const [failed, setFailed] = useState(false);
  const image = safePreviewUrl(mod.preview_url);
  return <div className={`mod-artwork ${large ? "large" : ""}`}>
    {image && !failed ? <Image src={image} width={800} height={450} alt={mod.title} unoptimized onError={() => setFailed(true)} loading={large ? "eager" : "lazy"}/> : <div className="artwork-fallback"><Mark/><span>FROM THE OLD WORLD ARCHIVES</span></div>}
    <span className="artwork-category">{categoryName(mod.category)}</span>
  </div>;
}

export function ModCard({ mod }: { mod: Mod }) {
  return <article className="mod-card">
    <div className="card-image-wrap"><Link href={`/mods/${mod.id}`} tabIndex={-1} aria-hidden="true"><ModArtwork mod={mod}/></Link><SaveButton mod={mod}/></div>
    <div className="card-body">
      <div className="card-meta"><span>{mod.summary_status === "generated" ? "中文解读" : mod.summary_status === "source_chinese" ? "作者中文说明" : "作者原文"}</span><span>{mod.chinese_support === "unknown" ? "汉化待确认" : mod.chinese_support === "translation_available" ? "有配套汉化" : "作者声明中文"}</span></div>
      <h3><Link href={`/mods/${mod.id}`}>{mod.title}</Link></h3>
      <p className="card-description">{mod.summary_zh || mod.description_excerpt || "作者说明尚未收录，可前往 Workshop 查看。"}</p>
      <div className="tag-row">{mod.tags.slice(0, 3).map((tag) => <span className="tag" key={tag}>{tag}</span>)}</div>
      <div className="card-footer"><span><Icon name="clock"/>{formatDate(mod.updated_at)}</span>{mod.subscribers !== null && mod.subscribers !== undefined ? <span title="来源记录的当前订阅数">{count(mod.subscribers)} 订阅</span> : <Link href={`/mods/${mod.id}`}>查看依据 <Icon name="arrow"/></Link>}</div>
    </div>
  </article>;
}
