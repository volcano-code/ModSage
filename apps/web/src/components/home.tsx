"use client";

import Image from "next/image";
import Link from "next/link";
import { categoryName, count, formatDate } from "@/lib/api";
import { useRemote } from "@/lib/use-remote";
import type { Digest, ModPage, Stats } from "@/lib/types";
import { Icon, Mark } from "./icons";
import { ModCard } from "./mod-card";
import { SyncStatus } from "./sync-status";
import { EmptyState, ErrorNotice, LoadingCards, SectionHeading } from "./ui";

export function Home() {
  const stats = useRemote<Stats>("/stats");
  const digest = useRemote<Digest>("/digests/latest");
  const recent = useRemote<ModPage>("/mods?sort=updated&page=1&page_size=6");
  const sections = digest.data?.sections.filter((section) => section.items.length) ?? [];
  const popular = sections.find((section) => section.key === "popular");
  const dispatches = sections.filter((section) => section.key !== "popular");

  return <>
    <div className="page-kicker"><span>THE CAMPAIGN ARCHIVES</span><span className="kicker-rule"/><span>卷首 · 每日精选</span></div>
    <section className="hero" aria-labelledby="hero-title">
      <Image className="hero-painting" src="/media/old-world-citadel-v2.webp" alt="" fill priority sizes="(max-width: 1440px) 100vw, 1344px"/>
      <div className="hero-shade"/>
      <div className="hero-content">
        <div className="hero-label"><span/>来自旧世界的模组战报</div>
        <h1 id="hero-title">旧世界，<br/>值得一场<span>新战役。</span></h1>
        <p>帝国的战旗尚未落下，远方又传来新的号角。<br/>发现值得一玩的模组，为下一场远征增添可能。</p>
        <div className="hero-actions"><Link href="/recommend" className="button button-gold"><Icon name="swords"/>描述我想玩的体验<Icon name="arrow"/></Link><Link href="/explore" className="button button-outline">探索模组<Icon name="compass"/></Link></div>
        <div className="hero-footnote"><Icon name="book"/>中文解读 · 作者来源 · 你的战役清单</div>
      </div>
      <div className="hero-caption" aria-hidden="true"><span>THE OLD WORLD</span><small>EVERY CAMPAIGN TELLS A STORY</small></div>
      <div className="hero-corner corner-tl"/><div className="hero-corner corner-br"/>
    </section>

    <section className="stat-strip" aria-label="目录状态">
      <div><Icon name="layers"/><span><strong>{stats.data ? count(stats.data.total_mods) : "—"}</strong><small>已收录模组</small></span></div>
      <div><Icon name="book"/><span><strong>{stats.data ? count(stats.data.chinese_summaries) : "—"}</strong><small>已有中文介绍</small></span></div>
      <div className="stat-time"><Icon name="clock"/><span><strong>{stats.loading ? "读取采集时间…" : stats.error ? "采集时间暂不可用" : stats.data?.last_synced_at ? formatDate(stats.data.last_synced_at, true) : "尚无采集记录"}</strong><small>目录采集覆盖时间 · 北京时间</small></span></div>
      <div className="stat-source"><span className="source-badge">STEAM WORKSHOP</span><small>推荐范围以当前收录目录为准</small></div>
    </section>
    {stats.error && <ErrorNotice message={stats.error} onRetry={stats.reload}/>}
    <SyncStatus/>

    <section className="content-section daily-section">
      <SectionHeading eyebrow="I. THE DAILY DISPATCH" title="每日精选 · 远征战报" note={digest.data?.generated_at ? `最近一期 ${digest.data.date} · ${formatDate(digest.data.generated_at, true)} 编入档案` : "新发布、近期更新，以及值得关注的热门模组。"} href="/explore" action="翻阅全部档案"/>
      {digest.loading ? <LoadingCards/> : digest.error ? <ErrorNotice message={digest.error} onRetry={digest.reload}/> : !sections.length ? <EmptyState icon="scroll" title="新一期模组简报尚未生成"><p>内容收录后，这里会按来源时间整理值得关注的变化。</p><p>你可以先探索目录，或描述自己想要的游戏体验。</p></EmptyState> : <>
        <div className={`dispatch-layout ${popular ? "has-ledger" : ""}`}>
          <div className="dispatch-main">
            {(dispatches.length ? dispatches : popular ? [popular] : []).map((section) => <section className="digest-section" key={section.key}><div className="subsection-heading"><h3><Icon name={section.key === "new" ? "spark" : section.key === "popular" ? "crown" : "refresh"}/>{section.title}</h3><span>{section.items.length} 项记录</span></div><div className="mod-grid dispatch-grid">{section.items.slice(0, 2).map((mod) => <ModCard mod={mod} key={mod.id}/>)}</div></section>)}
          </div>
          {popular && <aside className="popular-ledger" aria-label="热门模组榜"><div className="ledger-heading"><span className="ledger-icon"><Icon name="crown"/></span><span className="eyebrow">THE HONOUR ROLL</span><h3>诸军所选</h3><p>收录目录 · 当前订阅榜</p></div><ol>{popular.items.slice(0, 6).map((mod, index) => <li key={mod.id}><span className="rank-number">{String(index + 1).padStart(2, "0")}</span><div><Link href={`/mods/${mod.id}`}>{mod.title}</Link><span>{categoryName(mod.category)}<i/>{count(mod.subscribers)} 订阅</span></div><Icon name="chevron"/></li>)}</ol><Link className="ledger-link" href="/explore?sort=popular">查看热门模组<Icon name="arrow"/></Link><p className="ledger-footnote">按已取得的当前订阅数排列。</p></aside>}
        </div>
        <div className="dispatch-notes">{digest.data?.source_cutoff && <p className="source-note"><Icon name="clock"/>本期数据截止：{formatDate(digest.data.source_cutoff, true)}（北京时间）</p>}{digest.data?.warnings?.length ? <details className="digest-notes"><summary><Icon name="book"/>本期收录说明<Icon name="chevron"/></summary><div>{digest.data.warnings.map((warning, i) => <p key={i}>{warning}</p>)}</div></details> : null}</div>
      </>}
    </section>

    <section className="discovery-banner"><div className="banner-seal"><Mark/></div><div><span className="eyebrow">THE WAR COUNCIL</span><h2>领主，你的下一步棋是什么？</h2><p>“想玩震旦，增加新兵种，不要大型重做。”<br/>写下构想，让参谋为你寻找合适的模组。</p></div><Link href="/recommend" className="button button-gold">前往战役参谋<Icon name="arrow"/></Link></section>

    <section className="content-section"><SectionHeading eyebrow="II. NEWS FROM THE WORKSHOP" title="工坊来信 · 近期更新" note="按作者更新时间排列，安装前请核对适用版本与前置要求。" href="/explore?sort=updated" action="查看近期更新"/>{recent.loading ? <LoadingCards/> : recent.error ? <ErrorNotice message={recent.error} onRetry={recent.reload}/> : recent.data?.items.length ? <div className="mod-grid">{recent.data.items.map((mod) => <ModCard mod={mod} key={mod.id}/>)}</div> : <EmptyState title="目录正在等待首批内容"><p>首次收录后，这里将展示来自 Workshop 的模组更新。</p></EmptyState>}</section>
  </>;
}
