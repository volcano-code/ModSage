"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSavedMods } from "@/lib/saved";
import { Icon, Mark } from "./icons";

const navigation = [{ href: "/", label: "每日精选", numeral: "I" }, { href: "/explore", label: "探索模组", numeral: "II" }, { href: "/recommend", label: "需求推荐", numeral: "III" }];

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const saved = useSavedMods();
  return <>
    <a href="#main" className="skip-link">跳到主要内容</a>
    <header className="site-header">
      <div className="header-masthead"><span>THE OLD WORLD · A MODDER’S CHRONICLE</span><span>全面战争：战锤 III · 中文模组指南</span></div>
      <div className="header-inner">
        <Link href="/" className="brand" aria-label="ModSage 首页"><Mark className="brand-mark"/><span><strong>MODSAGE</strong><small>旧世界 · 模组参谋</small></span></Link>
        <nav aria-label="主导航" className="main-nav">{navigation.map((item) => <Link href={item.href} key={item.href} className={pathname === item.href ? "active" : ""} aria-current={pathname === item.href ? "page" : undefined}><span aria-hidden="true">{item.numeral}</span>{item.label}</Link>)}</nav>
        <Link href="/collection" aria-label="我的清单" aria-description={`已保存 ${saved.length} 个模组`} className={`collection-link ${pathname === "/collection" ? "active" : ""}`}><Icon name="bookmark"/><span>我的清单</span><b>{saved.length}</b></Link>
      </div>
    </header>
    <main id="main" className="page-shell">{children}</main>
    <footer className="site-footer"><div className="footer-brand"><Mark className="footer-mark"/><span>MODSAGE<small>献给旧世界的每一位领主</small></span></div><div className="footer-note"><p>玩家独立项目 · 模组内容来自 Steam Workshop</p><p>与 Valve、Creative Assembly 无隶属关系。创作属于每一位模组作者。</p></div><Link href="/explore">下一场战役，由你书写 <Icon name="arrow"/></Link></footer>
  </>;
}
