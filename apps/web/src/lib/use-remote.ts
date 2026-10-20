"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

export function useRemote<T>(path: string | null) {
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<{ key: string; data?: T; error?: string }>({ key: "" });
  const key = `${path}:${revision}`;

  useEffect(() => {
    if (path === null) return;
    const controller = new AbortController();
    api<T>(path, { signal: controller.signal }).then(
      (data) => { if (!controller.signal.aborted) setState({ key, data }); },
      (error: unknown) => {
        if (!controller.signal.aborted) setState({ key, error: error instanceof Error ? error.message : "请求未能完成，请重试。" });
      },
    );
    return () => controller.abort();
  }, [path, key]);

  const reload = useCallback(() => setRevision((value) => value + 1), []);
  return { data: state.key === key ? state.data : undefined, error: state.key === key ? state.error : undefined, loading: path !== null && state.key !== key, reload };
}
