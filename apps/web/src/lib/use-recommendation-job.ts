"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";
import {
  JOB_HISTORY_LIMIT, jobFailureMessage, loadJobHistory, newJobRecord,
  requestJob, saveJobHistory, streamJobEvents, terminalJob,
  type LocalJobHistory, type LocalJobRecord,
} from "./recommendation-jobs";
import type { RecommendationJob } from "./types";

export type JobConnection = "idle" | "connecting" | "connected" | "reconnecting" | "offline" | "closed";
export type JobOperation = "submit" | "cancel" | "retry" | null;

function pause(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) { resolve(); return; }
    const finish = () => { clearTimeout(timer); signal.removeEventListener("abort", finish); resolve(); };
    const timer = setTimeout(finish, milliseconds);
    signal.addEventListener("abort", finish, { once: true });
  });
}

export function useRecommendationJob() {
  const [history, setHistory] = useState<LocalJobHistory>({ version: 1, selected_key: null, items: [] });
  const [job, setJob] = useState<RecommendationJob | null>(null);
  const [connection, setConnection] = useState<JobConnection>("idle");
  const [operation, setOperation] = useState<JobOperation>(null);
  const [error, setError] = useState<string | null>(null);
  const [storageWarning, setStorageWarning] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const historyRef = useRef(history);
  const jobRef = useRef<RecommendationJob | null>(null);
  const generationRef = useRef(0);
  const controllerRef = useRef<AbortController | null>(null);
  const actionControllerRef = useRef<AbortController | null>(null);
  const operationRef = useRef<JobOperation>(null);

  const updateHistory = useCallback((next: LocalJobHistory) => {
    historyRef.current = next;
    setHistory(next);
    if (!saveJobHistory(next)) setStorageWarning("此浏览器无法保存推荐记录。当前任务仍可继续，请保持页面打开以查看结果。");
  }, []);

  const setAction = useCallback((next: JobOperation) => {
    operationRef.current = next;
    setOperation(next);
  }, []);

  const applySnapshot = useCallback((snapshot: RecommendationJob, key: string, generation: number) => {
    if (generation !== generationRef.current) return;
    const current = jobRef.current;
    if (current?.id === snapshot.id && current.last_event_id >= snapshot.last_event_id) return;
    jobRef.current = snapshot;
    setJob(snapshot);
    const previous = historyRef.current;
    const record = previous.items.find((item) => item.key === key);
    if (record && (record.id !== snapshot.id || record.last_status !== snapshot.status)) {
      updateHistory({ ...previous, items: previous.items.map((item) => item.key === key ? { ...item, id: snapshot.id, last_status: snapshot.status } : item) });
    }
    setConnection((previous) => terminalJob(snapshot) ? "closed" : previous === "closed" ? "connected" : previous);
  }, [updateHistory]);

  const watch = useCallback(async (record: LocalJobRecord, generation: number, controller: AbortController) => {
    let failures = 0;
    while (!controller.signal.aborted && generation === generationRef.current && jobRef.current && !terminalJob(jobRef.current)) {
      try {
        await streamJobEvents(record, jobRef.current.last_event_id, controller.signal,
          (snapshot) => applySnapshot(snapshot, record.key, generation),
          () => { if (generation === generationRef.current && !controller.signal.aborted) setConnection("connected"); });
        if (controller.signal.aborted || generation !== generationRef.current || !jobRef.current || terminalJob(jobRef.current)) return;
        // A bounded server stream rotates normally; retain the durable cursor.
        failures = 0;
        await pause(1000, controller.signal);
      } catch (failure) {
        if (controller.signal.aborted || generation !== generationRef.current) return;
        if (failure instanceof ApiError && [400, 401, 403, 404, 409, 422].includes(failure.status)) {
          setConnection("offline"); setError(jobFailureMessage(failure)); return;
        }
        setConnection("reconnecting");
        failures += 1;
        await pause(Math.min(8000, 1000 * 2 ** Math.min(failures - 1, 3)), controller.signal);
      }
    }
  }, [applySnapshot]);

  const activate = useCallback(async (record: LocalJobRecord, seed?: RecommendationJob) => {
    controllerRef.current?.abort();
    const generation = ++generationRef.current;
    const controller = new AbortController();
    controllerRef.current = controller;
    jobRef.current = null;
    setJob(null); setError(null); setConnection("connecting");
    updateHistory({ ...historyRef.current, selected_key: record.key });
    if (!record.id) setAction("submit");
    try {
      const snapshot = seed ?? await requestJob(record, record.id ? "get" : "create", controller.signal);
      if (generation !== generationRef.current || controller.signal.aborted) return;
      applySnapshot(snapshot, record.key, generation);
      setAction(null);
      if (terminalJob(snapshot)) return;
      await watch({ ...record, id: snapshot.id }, generation, controller);
    } catch (failure) {
      if (generation !== generationRef.current || controller.signal.aborted) return;
      setConnection("offline");
      if (!record.id && failure instanceof ApiError && failure.status >= 400 && failure.status < 500 && ![408, 429].includes(failure.status)) {
        // A definite admission rejection is not an accepted/background task.
        const previous = historyRef.current;
        updateHistory({ ...previous, selected_key: null, items: previous.items.filter((item) => item.key !== record.key) });
        setError(jobFailureMessage(failure, "提交未成功，请稍后重试。"));
      } else {
        // Rate limiting can precede idempotency lookup, so a 429 replay must keep its recovery credentials.
        setError(jobFailureMessage(failure, record.id ? "暂时无法读取任务，请重新连接。" : "尚未确认提交结果。请恢复提交以核对原任务，避免重复创建。"));
      }
    } finally {
      if (generation === generationRef.current && operationRef.current === "submit") setAction(null);
    }
  }, [applySnapshot, setAction, updateHistory, watch]);

  const stopWatching = useCallback(() => {
    ++generationRef.current;
    controllerRef.current?.abort();
    actionControllerRef.current?.abort();
  }, []);

  useEffect(() => {
    let disposed = false;
    void Promise.resolve().then(() => {
      if (disposed) return;
      const loaded = loadJobHistory();
      historyRef.current = loaded.history;
      setHistory(loaded.history); setStorageWarning(loaded.warning); setReady(true);
      const selected = loaded.history.items.find((item) => item.key === loaded.history.selected_key);
      if (selected) void activate(selected);
    });
    return () => {
      disposed = true;
      stopWatching();
    };
  }, [activate, stopWatching]);

  const submit = useCallback((query: string) => {
    if (operationRef.current || query.trim().length < 2 || query.trim().length > 1000) return;
    let items = historyRef.current.items;
    if (items.length >= JOB_HISTORY_LIMIT) {
      const oldestCompleted = items.findLastIndex((item) => item.last_status && ["completed", "failed", "cancelled"].includes(item.last_status));
      if (oldestCompleted < 0) { setError("本机已有 20 条待处理记录，请先查看或移除旧记录。移除记录不会取消后台任务。"); return; }
      items = items.filter((_, index) => index !== oldestCompleted);
    }
    try {
      const record = newJobRecord(query);
      // Persist the intent before POST: a lost response can replay the same key and token.
      updateHistory({ version: 1, selected_key: record.key, items: [record, ...items] });
      void activate(record);
    } catch {
      setError("此浏览器暂时无法建立任务，请在安全连接或本机环境中重试。");
    }
  }, [activate, updateHistory]);

  const select = useCallback((key: string) => {
    if (operationRef.current) return;
    const record = historyRef.current.items.find((item) => item.key === key);
    if (record) void activate(record);
  }, [activate]);

  const reconnect = useCallback(() => {
    if (operationRef.current) return;
    const record = historyRef.current.items.find((item) => item.key === historyRef.current.selected_key);
    if (record) void activate(record);
  }, [activate]);

  const changeJob = useCallback(async (action: "cancel" | "retry") => {
    if (operationRef.current) return;
    const record = historyRef.current.items.find((item) => item.key === historyRef.current.selected_key);
    if (!record?.id || !jobRef.current) return;
    if (action === "cancel" && terminalJob(jobRef.current)) return;
    if (action === "retry" && (jobRef.current.status !== "failed" || jobRef.current.attempts >= jobRef.current.max_attempts)) return;
    const generation = generationRef.current;
    const controller = new AbortController();
    actionControllerRef.current = controller;
    setAction(action); setError(null);
    try {
      const snapshot = await requestJob(record, action, controller.signal);
      if (generation !== generationRef.current || controller.signal.aborted) return;
      setAction(null);
      if (action === "retry") void activate(record, snapshot);
      else {
        applySnapshot(snapshot, record.key, generation);
        if (terminalJob(snapshot)) controllerRef.current?.abort();
      }
    } catch (failure) {
      if (generation === generationRef.current && !controller.signal.aborted) {
        // The event stream may have confirmed cancellation before its POST response was lost.
        if (action === "cancel" && jobRef.current?.status === "cancelled") return;
        setError(jobFailureMessage(failure, action === "cancel" ? "取消请求尚未确认，请重新连接查看任务状态。" : "重试请求尚未确认，请重新连接查看任务状态。"));
      }
    } finally {
      if (generation === generationRef.current) setAction(null);
    }
  }, [activate, applySnapshot, setAction]);

  const newDraft = useCallback(() => {
    if (operationRef.current) return;
    controllerRef.current?.abort();
    ++generationRef.current;
    jobRef.current = null; setJob(null); setError(null); setConnection("idle");
    updateHistory({ ...historyRef.current, selected_key: null });
  }, [updateHistory]);

  const forget = useCallback((key: string) => {
    if (operationRef.current) return;
    const previous = historyRef.current;
    if (previous.selected_key === key) {
      controllerRef.current?.abort(); ++generationRef.current;
      jobRef.current = null; setJob(null); setError(null); setConnection("idle");
    }
    updateHistory({ ...previous, selected_key: previous.selected_key === key ? null : previous.selected_key, items: previous.items.filter((item) => item.key !== key) });
  }, [updateHistory]);

  return {
    history, job, connection, operation, error, storageWarning, ready,
    selected: history.items.find((item) => item.key === history.selected_key) ?? null,
    submit, select, reconnect, cancel: () => changeJob("cancel"), retry: () => changeJob("retry"), newDraft, forget,
  };
}
