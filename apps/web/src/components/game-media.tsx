"use client";

import Image from "next/image";
import Link from "next/link";
import { useState } from "react";
import { Icon } from "./icons";
import { SectionHeading } from "./ui";

const gameSource = "https://store.steampowered.com/app/1142710/Total_War_WARHAMMER_III/";
const empireSource = "https://store.steampowered.com/app/2325832/Total_War_WARHAMMER_III_Elspeth_Thrones_of_Decay/";

const gameMedia = {
  realms: {
    image: "/media/warhammer3/official-key-art.jpg",
    title: "冰雪与混沌",
    caption: "TOTAL WAR: WARHAMMER III",
    alt: "《全面战争：战锤 III》官方主视觉：冰雪女王、基斯里夫军阵与混沌恶魔",
    position: "62% 50%",
    source: gameSource,
  },
  cathay: {
    image: "/media/warhammer3/cathay-battle.jpg",
    title: "震旦 · 长垣之战",
    caption: "GRAND CATHAY",
    alt: "《全面战争：战锤 III》官方画面：震旦军队在长垣前迎战混沌",
    position: "55% 45%",
    source: gameSource,
  },
  empire: {
    image: "/media/warhammer3/empire-elspeth.jpg",
    title: "帝国 · 魔法与火药",
    caption: "THE EMPIRE",
    alt: "《全面战争：战锤 III》官方 DLC 画面：艾斯佩斯与帝国军阵",
    position: "53% 38%",
    source: empireSource,
  },
  kislev: {
    image: "/media/warhammer3/kislev-ice-court.jpg",
    title: "基斯里夫 · 冰雪宫廷",
    caption: "KISLEV",
    alt: "《全面战争：战锤 III》官方画面：基斯里夫冰雪女王、冰霜女巫与雪豹",
    position: "64% 42%",
    source: gameSource,
  },
  khorne: {
    image: "/media/warhammer3/khorne-battle.jpg",
    title: "恐虐 · 熔火战场",
    caption: "THE REALMS OF CHAOS",
    alt: "《全面战争：战锤 III》官方画面：恐虐军队与嗜血魔",
    position: "68% 43%",
    source: gameSource,
  },
} as const;

type SceneName = keyof typeof gameMedia;
const heroScenes: SceneName[] = ["realms", "cathay", "empire"];

export function CampaignHero() {
  const [selected, setSelected] = useState<SceneName>("realms");
  const scene = gameMedia[selected];

  return <>
    <section className="hero" aria-labelledby="hero-title">
      <div className="hero-art">
        <Image className="hero-painting" src={scene.image} alt={scene.alt} fill priority sizes="(max-width: 720px) 100vw, (max-width: 1440px) 78vw, 1050px" style={{ objectPosition: scene.position }}/>
      </div>
      <div className="hero-shade"/>
      <div className="hero-content">
        <div className="hero-label"><span/>全面战争：战锤 III · 模组指南</div>
        <h1 id="hero-title">旧世界，<br/>值得一场<span>新战役。</span></h1>
        <p>从帝国的火炮，到震旦的长垣。<br/>为你熟悉的战场，寻找新的玩法。</p>
        <div className="hero-actions">
          <Link href="/recommend" className="button button-gold"><Icon name="swords"/>描述我想玩的体验<Icon name="arrow"/></Link>
          <Link href="/explore" className="button button-outline">探索模组<Icon name="compass"/></Link>
        </div>
        <div className="hero-footnote"><Icon name="book"/>中文解读 · 作者来源 · 你的战役清单</div>
      </div>
      <div className="hero-caption" aria-live="polite">
        <span>{scene.caption}</span>
        <strong>{scene.title}</strong>
        <a href={scene.source} target="_blank" rel="noopener noreferrer">官方游戏画面 · 查看来源<Icon name="external"/></a>
      </div>
      <div className="hero-corner corner-tl"/><div className="hero-corner corner-br"/>
    </section>
    <div className="battlefield-switcher" role="group" aria-label="选择战役画面">
      <span className="battlefield-label">旧世界的战场<small>CHOOSE YOUR SCENE</small></span>
      {heroScenes.map((name, index) => {
        const item = gameMedia[name];
        return <button type="button" key={name} aria-pressed={selected === name} onClick={() => setSelected(name)} className={selected === name ? "selected" : ""}>
          <span className="scene-thumbnail"><Image src={item.image} alt="" fill sizes="72px" style={{ objectPosition: item.position }}/></span>
          <span><small>0{index + 1}</small><strong>{item.title}</strong></span>
          <Icon name="chevron"/>
        </button>;
      })}
    </div>
  </>;
}

const factions = [
  { scene: "empire" as const, name: "帝国", query: "帝国", description: "魔法、火药与凡人之勇", sourceLabel: "艾斯佩斯 · 官方 DLC 画面" },
  { scene: "cathay" as const, name: "震旦天朝", query: "震旦", description: "长垣、天灯与龙之军团", sourceLabel: "震旦长垣 · 官方游戏画面" },
  { scene: "kislev" as const, name: "基斯里夫", query: "基斯里夫", description: "冰霜魔法与北境守军", sourceLabel: "冰雪宫廷 · 官方游戏画面" },
];

export function FactionGateways() {
  return <section className="content-section faction-section" aria-label="按派系探索模组">
    <SectionHeading eyebrow="THE FACTIONS OF THE OLD WORLD" title="这一次，为谁而战？" note="从熟悉的派系出发，探索对应的模组与玩法。" href="/explore" action="探索全部派系"/>
    <div className="faction-grid">{factions.map((faction) => {
      const scene = gameMedia[faction.scene];
      return <Link className={`faction-card faction-${faction.scene}`} href={`/explore?${new URLSearchParams({ q: faction.query, sort: "relevant" })}`} key={faction.scene} aria-label={`探索${faction.name}模组`}>
        <div className="faction-art"><Image src={scene.image} alt={scene.alt} fill sizes="(max-width: 720px) 100vw, (max-width: 1440px) 33vw, 430px" style={{ objectPosition: scene.position }}/></div>
        <span className="faction-source">{faction.sourceLabel}</span>
        <div className="faction-copy"><span className="eyebrow">{scene.caption}</span><h3>{faction.name}</h3><p>{faction.description}</p><span className="faction-action">探索相关模组<Icon name="arrow"/></span></div>
      </Link>;
    })}</div>
    <p className="game-media-note">派系画面来自官方游戏素材；下方模组预览由各自 Workshop 作者提供。<a href={gameSource} target="_blank" rel="noopener noreferrer">查看游戏<Icon name="external"/></a></p>
  </section>;
}

export function CampaignBanner({ scene, eyebrow, title, description }: { scene: SceneName; eyebrow: string; title: string; description: string }) {
  const media = gameMedia[scene];
  return <header className="page-heading campaign-heading">
    <div className="campaign-heading-art"><Image src={media.image} alt="" fill sizes="(max-width: 720px) 100vw, 65vw" style={{ objectPosition: media.position }}/></div>
    <div className="campaign-heading-shade"/>
    <div className="campaign-heading-copy"><span className="eyebrow">{eyebrow}</span><h1>{title}</h1><p>{description}</p></div>
    <a className="campaign-source" href={media.source} target="_blank" rel="noopener noreferrer">官方游戏画面<Icon name="external"/></a>
  </header>;
}

export function GameMediaCredits() {
  return <p className="game-media-credits">游戏画面来自 <a href={gameSource} target="_blank" rel="noopener noreferrer">《全面战争：战锤 III》</a>及其官方 DLC，相关权利归 Creative Assembly、SEGA 与 Games Workshop 所有。</p>;
}
