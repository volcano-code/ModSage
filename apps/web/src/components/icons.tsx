import type { SVGProps } from "react";

export type IconName = "compass" | "arrow" | "search" | "spark" | "bookmark" | "external" | "clock" | "layers" | "check" | "alert" | "refresh" | "x" | "download" | "shield" | "chevron" | "book";

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
};

export function Icon({ name, ...props }: SVGProps<SVGSVGElement> & { name: IconName }) {
  return <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{paths[name]}</svg>;
}

export function Mark({ className = "" }: { className?: string }) {
  return <svg viewBox="0 0 80 80" fill="none" aria-hidden="true" className={className}><path d="M40 7 69 24v32L40 73 11 56V24L40 7Z" stroke="currentColor" strokeWidth="2"/><path d="M24 53V28l16 14 16-14v25M40 42v18" stroke="currentColor" strokeWidth="3" strokeLinejoin="round"/></svg>;
}
