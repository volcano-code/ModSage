import type { SVGProps } from "react";

export type IconName = "compass" | "arrow" | "search" | "spark" | "bookmark" | "external" | "clock" | "layers" | "check" | "alert" | "refresh" | "x" | "download" | "shield" | "chevron" | "book" | "swords" | "crown" | "scroll";

const paths: Record<IconName, React.ReactNode> = {
  compass: <><circle cx="12" cy="12" r="9"/><path d="m16 8-2.5 5.5L8 16l2.5-5.5L16 8Z"/></>,
  arrow: <><path d="M4 12h15m-6-6 6 6-6 6"/></>,
  search: <><circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/></>,
  spark: <><path d="m12 3 2.4 6.6L21 12l-6.6 2.4L12 21l-2.4-6.6L3 12l6.6-2.4L12 3Z"/><path d="M20 2v4m-2-2h4"/></>,
  bookmark: <path d="M6 4h12v17l-6-4-6 4V4Z"/>,
  external: <><path d="M14 4h6v6m0-6L10 14"/><path d="M10 4H4v16h16v-6"/></>,
  clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
  layers: <><path d="m3 8 9-5 9 5-9 5-9-5Zm0 5 9 5 9-5m-18 5 9 5 9-5"/></>,
  check: <path d="m5 12 4 4L19 6"/>,
  alert: <><path d="m12 3 10 18H2L12 3Z"/><path d="M12 9v5m0 3v.01"/></>,
  refresh: <><path d="M20 6v5h-5M4 18v-5h5"/><path d="M6 7a7 7 0 0 1 11-2l3 4M4 15l3 4a7 7 0 0 0 11-2"/></>,
  x: <path d="m6 6 12 12M6 18 18 6"/>,
  download: <><path d="M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5"/></>,
  shield: <><path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6l8-3Z"/><path d="m8 12 3 3 5-6"/></>,
  chevron: <path d="m9 5 7 7-7 7"/>,
  book: <><path d="M12 6v15M3 4c5 0 7 1 9 3 2-2 4-3 9-3v15c-5 0-7 0-9 2-2-2-4-2-9-2V4Z"/></>,
  swords: <><path d="m4 3 5 2 11 13-2 2L5 9 4 3Zm16 0-5 2-4 5m-1 3-5 5 2 2 5-5M3 16l5 5m8-18-1 6M3 21l3-3m12 0 3 3m-5-2 5-5"/></>,
  crown: <><path d="m3 6 5 4 4-6 4 6 5-4-3 12H6L3 6Zm3 15h12M6 15h12"/><path d="M12 2v1"/></>,
  scroll: <><path d="M6 3h13a2 2 0 0 1 2 2v3h-4V5a2 2 0 0 1 2-2M6 3a3 3 0 0 0-3 3v12m3-15a3 3 0 0 1 3 3v12m-6 0h12v1a3 3 0 0 1-3 3H6a3 3 0 0 1-3-3v-1m12-4h2V8M12 8h2m-2 4h2"/></>,
};

export function Icon({ name, ...props }: SVGProps<SVGSVGElement> & { name: IconName }) {
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{paths[name]}</svg>;
}

export function Mark({ className = "" }: { className?: string }) {
  return <svg viewBox="0 0 100 100" fill="none" aria-hidden="true" className={className}>
    <g fill="currentColor"><path d="M33 38 8 14l3 18 18 16-21-9 6 15 17 8-16-3 7 12 13 1 4-28-6-6Zm34 0 25-24-3 18-18 16 21-9-6 15-17 8 16-3-7 12-13 1-4-28 6-6Z"/><path d="m50 3 3 8 8 3-8 3-3 8-3-8-8-3 8-3 3-8Z"/></g>
    <path d="m50 26 20 8-3 28c-1 13-17 25-17 25S34 75 33 62l-3-28 20-8Z" fill="#231a16" stroke="currentColor" strokeWidth="2.5"/>
    <path d="m50 32 14 6-2 23c-1 8-12 19-12 19S39 69 38 61l-2-23 14-6Z" stroke="currentColor" strokeWidth=".8"/>
    <path d="M47 45h6v26h-6zM39 41h22v10H39z" fill="currentColor"/><path d="M39 40v12m22-12v12M45 71h10" stroke="currentColor" strokeWidth="2.5"/>
    <path d="m24 78 10 2m42-2-10 2M37 88l13 7 13-7" stroke="currentColor" strokeWidth="1.5"/>
  </svg>;
}
