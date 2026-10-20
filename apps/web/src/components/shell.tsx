"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useSavedMods } from "@/lib/saved";
import { Icon, Mark } from "./icons";

const navigation = [{ href: "/", label: "每日精选" }, { href: "/explore", label: "探索模组" }, { href: "/recommend", label: "需求推荐" }];

export function Shell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const saved = useSavedMods();
  return <>
    <a href="#main" className="skip-link">跳到主要内容</a>
    <header className="site-header">
      <div className="header-inner">
        <Link href="/" className="brand" aria-label="ModSage 首页"><Mark className="brand-mark"/><span><strong>MODSAGE<span className="brand-dot">.</span></strong><small>战锤模组参谋</small></span></Link>
        <nav aria-label="主导航" className="main-nav">{navigation.map((item) => <Link href={item.href} key={item.href} className={pathname === item.href ? "active" : ""} aria-current={pathname === item.href ? "page" : undefined}>{item.label}</Link>)}</nav>
        <Link href="/collection" aria-label="我的清单" aria-description={`已保存 ${saved.length} 个模组`} className={`collection-link ${pathname === "/collection" ? "active" : ""}`}><Icon name="bookmark"/><span>我的清单</span><b>{saved.length}</b></Link>
      </div>
    </header>
    <main id="main" className="page-shell">{children}</main>
    <footer className="site-footer"><div><Mark className="footer-mark"/><span>MODSAGE<span className="muted"> · 发现好模组，配出好战役。</span></span></div><p>玩家独立项目 · 内容来自 Steam Workshop · 与 Valve、Creative Assembly 无隶属关系</p><Link href="/explore">探索你的下一场战役 <Icon name="arrow"/></Link></footer>
  </>;
}
