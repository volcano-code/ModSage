import type { Metadata } from "next";
import { Shell } from "@/components/shell";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "ModSage · 战锤模组参谋", template: "%s · ModSage" },
  description: "面向《全面战争：战锤 3》中文玩家的 Mod 发现与搭配助手。查看来源、理解改动，为下一场战役找到合适的模组。",
  icons: { icon: "/mark.svg" },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body><Shell>{children}</Shell></body></html>;
}
