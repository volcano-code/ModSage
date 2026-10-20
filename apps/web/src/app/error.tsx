"use client";

import { ErrorNotice } from "@/components/ui";

export default function ErrorPage({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return <div className="page-heading"><h1>页面遇到了一点问题。</h1><ErrorNotice message="你可以重试，或返回模组目录继续浏览。" onRetry={reset}/></div>;
}
