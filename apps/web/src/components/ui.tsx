"use client";

import Link from "next/link";
import { Icon, type IconName } from "./icons";

export function ErrorNotice({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return <div className="notice error-notice" role="alert"><Icon name="alert"/><div><strong>暂时无法读取数据</strong><p>{message}</p></div>{onRetry && <button className="button button-small button-quiet" onClick={onRetry}><Icon name="refresh"/>重试</button>}</div>;
}

export function EmptyState({ icon = "compass", title, children, href, action }: { icon?: IconName; title: string; children: React.ReactNode; href?: string; action?: string }) {
  return <div className="empty-state"><span className="empty-icon"><Icon name={icon}/></span><h3>{title}</h3><div className="empty-description">{children}</div>{href && <Link href={href} className="button button-quiet">{action ?? "去探索模组"}<Icon name="arrow"/></Link>}</div>;
}

export function LoadingCards({ count = 3 }: { count?: number }) {
  return <div className="mod-grid" aria-busy="true" aria-label="正在加载模组">{Array.from({ length: count }, (_, i) => <div className="skeleton-card" key={i}><div className="skeleton skeleton-image"/><div className="skeleton skeleton-line"/><div className="skeleton skeleton-line short"/><div className="skeleton skeleton-line"/></div>)}</div>;
}

export function SectionHeading({ eyebrow, title, note, href, action }: { eyebrow?: string; title: string; note?: string; href?: string; action?: string }) {
  return <div className="section-heading"><div>{eyebrow && <span className="eyebrow">{eyebrow}</span>}<h2>{title}</h2>{note && <p>{note}</p>}</div>{href && <Link href={href} className="text-link">{action ?? "查看全部"}<Icon name="arrow"/></Link>}</div>;
}
