"use client";

import Link from "next/link";
import { count, formatDate } from "@/lib/api";
import { useRemote } from "@/lib/use-remote";
import type { Digest, ModPage, Stats } from "@/lib/types";
import { Icon, Mark } from "./icons";
import { ModCard } from "./mod-card";
import { EmptyState, ErrorNotice, LoadingCards, SectionHeading } from "./ui";

export function Home() {
  const stats = useRemote<Stats>("/stats");
  const digest = useRemote<Digest>("/digests/latest");
  const recent = useRemote<ModPage>("/mods?sort=updated&page=1&page_size=6");
  const hasDigest = Boolean(digest.data?.sections.some((section) => section.items.length));

  return <>
    <div className="page-kicker"><span className="status-dot"/>TOTAL WAR: WARHAMMER III<span className="kicker-divider"/>中文玩家的模组指南</div>
    <section className="hero">
      <div className="hero-content"><div className="hero-label"><Icon name="compass"/>每一场战役，都值得新的发现</div><h1>找到好模组，<br/>开启你的<span>下一场战役。</span></h1><p>从作者说明到中文解读，从一句想法到你的模组清单。<br className="desktop-break"/>让每一次选择，都有值得信赖的依据。</p><div className="hero-actions"><Link href="/recommend" className="button button-gold"><Icon name="spark"/>描述我想玩的体验<Icon name="arrow"/></Link><Link href="/explore" className="button button-outline">探索模组<Icon name="compass"/></Link></div><div className="hero-footnote"><Icon name="shield"/>保留原始来源 · 标明未知信息 · 尊重作者创作</div></div>
      <div className="hero-emblem" aria-hidden="true"><div className="orbit orbit-one"/><div className="orbit orbit-two"/><div className="emblem-axis axis-one"/><div className="emblem-axis axis-two"/><span className="emblem-north">N</span><Mark className="hero-mark"/><div className="emblem-caption">THE ART OF DISCOVERY<span>YOUR CAMPAIGN. YOUR CHOICE.</span></div></div>
    </section>

    <section className="stat-strip" aria-label="目录状态"><div><Icon name="layers"/><span><strong>{stats.data ? count(stats.data.total_mods) : "—"}</strong><small>已收录模组</small></span></div><div><Icon name="book"/><span><strong>{stats.data ? count(stats.data.chinese_summaries) : "—"}</strong><small>已有中文介绍</small></span></div><div className="stat-time"><Icon name="clock"/><span><strong>{stats.loading ? "读取采集时间…" : stats.error ? "采集时间暂不可用" : stats.data?.last_synced_at ? formatDate(stats.data.last_synced_at, true) : "尚无采集记录"}</strong><small>目录采集覆盖时间 · 北京时间</small></span></div><div className="stat-source"><span className="source-badge">STEAM WORKSHOP</span><small>推荐范围以当前收录目录为准</small></div></section>
    {stats.error && <ErrorNotice message={stats.error} onRetry={stats.reload}/>}

    <section className="content-section"><SectionHeading eyebrow="THE DAILY BRIEF" title="每日精选" note={digest.data?.generated_at ? `最近一期：${digest.data.date} · 生成于 ${formatDate(digest.data.generated_at, true)}` : "分别发现新发布、近期更新与有数据依据的热门内容。"} href="/explore"/>
      {digest.loading ? <LoadingCards/> : digest.error ? <ErrorNotice message={digest.error} onRetry={digest.reload}/> : !hasDigest ? <EmptyState icon="book" title="新一期模组简报尚未生成"><p>内容收录后，这里会按来源时间整理值得关注的变化。</p><p>你可以先探索目录，或描述自己想要的游戏体验。</p></EmptyState> : <>
        {digest.data?.warnings?.length ? <div className="notice"><Icon name="alert"/><div>{digest.data.warnings.map((warning, i) => <p key={i}>{warning}</p>)}</div></div> : null}
        {digest.data?.sections.filter((section) => section.items.length).map((section) => <div className="digest-section" key={section.key}><div className="subsection-heading"><h3>{section.title}</h3><span>{section.items.length} 项收录内容</span></div><div className="mod-grid">{section.items.slice(0, 3).map((mod) => <ModCard mod={mod} key={mod.id}/>)}</div></div>)}
        {digest.data?.source_cutoff && <p className="source-note"><Icon name="clock"/>本期数据截止：{formatDate(digest.data.source_cutoff, true)}（北京时间）</p>}
      </>}
    </section>

    <section className="discovery-banner"><span className="banner-icon"><Icon name="spark"/></span><div><span className="eyebrow">TELL US YOUR NEXT CAMPAIGN</span><h2>有想法，但不知道该搜什么？</h2><p>试着说：“我想玩震旦，增加新兵种，不要大型重做。”</p></div><Link href="/recommend" className="button button-gold">让参谋帮你找<Icon name="arrow"/></Link></section>

    <section className="content-section"><SectionHeading eyebrow="FROM THE WORKSHOP" title="目录中的最近更新" note="按作者的更新时间排列；更新时间不代表已确认兼容当前游戏版本。" href="/explore?sort=updated"/>{recent.loading ? <LoadingCards/> : recent.error ? <ErrorNotice message={recent.error} onRetry={recent.reload}/> : recent.data?.items.length ? <div className="mod-grid">{recent.data.items.map((mod) => <ModCard mod={mod} key={mod.id}/>)}</div> : <EmptyState title="目录正在等待首批内容"><p>目前还没有已收录的 Mod。这里会展示真实来源记录，不使用演示条目。</p></EmptyState>}</section>
  </>;
}
