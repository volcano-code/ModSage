"use client";

import { useState } from "react";
import { formatDate } from "@/lib/api";
import type { LocalJobRecord } from "@/lib/recommendation-jobs";
import type { JobConnection, JobOperation } from "@/lib/use-recommendation-job";
import type { RecommendationJob, RecommendationJobStatus } from "@/lib/types";
import { Icon } from "./icons";

const statusLabels: Record<RecommendationJobStatus, string> = {
  queued: "等待接手", running: "执行中", completed: "推荐已完成", failed: "任务未完成", cancelled: "任务已取消",
};
const steps = [
  { key: "queued", title: "等待接手", description: "任务保存到队列" },
  { key: "understand", title: "理解需求", description: "整理偏好与排除项" },
  { key: "retrieve", title: "检索与筛选", description: "寻找符合条件的模组" },
  { key: "verify", title: "核对来源", description: "检查候选与事实依据" },
];
const failureLabels: Record<string, string> = {
  RequestBudgetExceeded: "本次执行超过时间预算，可以保留条件重试。",
  WorkerError: "后台未能完成本次推荐，已保留任务记录。",
  LeaseExpired: "后台执行未能及时恢复，任务记录已保存。",
  WorkerInterrupted: "执行进程中断，任务记录已保存。",
  attempts_exhausted: "本任务已达到执行次数上限，请查看条件后新建推荐。",
};

export function RecommendationJobPanel({ record, job, connection, operation, hasError, onCancel, onRetry, onReconnect, onNew }: {
  record: LocalJobRecord;
  job: RecommendationJob | null;
  connection: JobConnection;
  operation: JobOperation;
  hasError: boolean;
  onCancel: () => void;
  onRetry: () => void;
  onReconnect: () => void;
  onNew: () => void;
}) {
  const active = job?.status === "queued" || job?.status === "running";
  const awaiting = !record.id;
  const currentIndex = job?.status === "completed" ? steps.length : steps.findIndex((step) => step.key === job?.stage);
  const heading = !job ? awaiting ? operation === "submit" ? "正在提交战役构想" : "提交结果待确认" : "正在读取任务"
    : job.status === "running" ? steps.find((step) => step.key === job.stage)?.title ?? "执行中" : statusLabels[job.status];
  const connectionLabel = connection === "reconnecting" ? "进度连接中断，正在自动恢复。后台任务仍会继续。"
    : connection === "offline" ? "暂时无法取得最新状态，可以重新连接核对。"
      : connection === "connecting" && record.id ? "正在连接任务记录…" : active ? "进度来自后台实际执行阶段；离开页面不会取消任务。" : null;

  return <section className={`job-panel job-panel-${job?.status ?? "pending"}`} aria-label="推荐任务进度" aria-busy={Boolean(operation)}>
    <div className="job-panel-heading"><div><span className="eyebrow">CAMPAIGN DISPATCH · 参谋军报</span><h2><Icon name={job?.status === "completed" ? "check" : job?.status === "failed" ? "alert" : job?.status === "cancelled" ? "x" : "compass"}/><span role="status" aria-live="polite">{heading}</span></h2></div><span className="job-attempt">{job?.attempts ? `第 ${job.attempts} 次执行` : "尚未开始执行"}</span></div>
    <p className="job-query">“{record.query}”</p>
    {job && <ol className="job-steps" aria-label="推荐执行阶段">{steps.map((step, index) => {
      const complete = currentIndex > index;
      const current = active && currentIndex === index;
      return <li key={step.key} className={complete ? "is-complete" : current ? "is-current" : ""} aria-current={current ? "step" : undefined}>
        <span className="job-step-number">{complete ? <Icon name="check"/> : String(index + 1).padStart(2, "0")}</span><span><strong>{step.title}</strong><small>{step.description}</small></span>
      </li>;
    })}</ol>}
    {job?.status === "queued" && <p className="job-explanation">任务已保存，正在等待后台接手。你可以稍后从本机记录中继续查看。</p>}
    {job?.status === "failed" && <p className="job-explanation">{job.attempts >= job.max_attempts ? failureLabels.attempts_exhausted : failureLabels[job.error_code ?? ""] ?? "本次任务未能完成。条件和运行记录已保留，可以重试。"}</p>}
    {job?.status === "cancelled" && <p className="job-explanation">服务端已确认取消，本任务不会再发布推荐结果。</p>}
    {!job && awaiting && operation !== "submit" && <p className="job-explanation">尚未获得服务端确认。恢复提交会核对同一条请求；不会另建一条相同任务。</p>}
    {connectionLabel && <p className={`job-connection ${connection === "reconnecting" || connection === "offline" ? "is-interrupted" : ""}`} role="status"><span/>{connectionLabel}</p>}
    <div className="job-panel-footer"><div className="job-reference">{job ? <><span>提交于 {formatDate(job.created_at, true)}</span><small>任务编号：{job.id}</small></> : <span>{awaiting ? "提交尚待核对" : "记录已保留在本机"}</span>}</div><div className="job-actions">
      {active && <button type="button" className="button button-outline" onClick={onCancel} disabled={Boolean(operation)}>{operation === "cancel" ? "正在确认取消" : "取消任务"}</button>}
      {job?.status === "failed" && job.attempts < job.max_attempts && <button type="button" className="button button-gold" onClick={onRetry} disabled={Boolean(operation)}><Icon name="refresh"/>{operation === "retry" ? "正在重试" : "重试任务"}</button>}
      {!job && awaiting && operation !== "submit" && <button type="button" className="button button-gold" onClick={onReconnect} disabled={Boolean(operation)}>恢复提交</button>}
      {record.id && (connection === "offline" || connection === "reconnecting" || hasError) && <button type="button" className="button button-outline" onClick={onReconnect} disabled={Boolean(operation)}>重新连接</button>}
      <button type="button" className="button button-quiet" onClick={onNew} disabled={Boolean(operation)}>新建推荐<Icon name="arrow"/></button>
    </div></div>
  </section>;
}

export function RecommendationHistory({ records, selectedKey, disabled, onSelect, onForget }: {
  records: LocalJobRecord[];
  selectedKey: string | null;
  disabled: boolean;
  onSelect: (key: string) => void;
  onForget: (key: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  if (!records.length) return null;
  const shown = expanded ? records : records.slice(0, 5);
  return <section className="job-history" aria-label="本机推荐记录">
    <div className="job-history-heading"><div><span className="eyebrow">RECENT DISPATCHES</span><h2>本机推荐记录 <small>{records.length}</small></h2></div><Icon name="book"/></div>
    <p>状态来自最近一次查看；点击记录可恢复进度或读取结果。</p>
    <ol>{shown.map((record) => <li key={record.key} className={record.key === selectedKey ? "is-selected" : ""}>
      <button type="button" className="job-history-select" aria-label={`查看推荐记录：${record.query}`} aria-pressed={record.key === selectedKey} disabled={disabled} onClick={() => onSelect(record.key)}><span>{record.query}</span><small>{formatDate(record.created_at, true)} · {record.last_status ? statusLabels[record.last_status] : "提交待确认"}</small></button>
      <button type="button" className="icon-button" aria-label={`移除本机记录：${record.query}`} title="仅移除本机记录，不取消后台任务" disabled={disabled} onClick={() => onForget(record.key)}><Icon name="x"/></button>
    </li>)}</ol>
    {records.length > 5 && <button type="button" className="text-link job-history-expand" onClick={() => setExpanded(!expanded)}>{expanded ? "收起记录" : `查看全部 ${records.length} 条记录`}<Icon name="chevron"/></button>}
    <p className="job-history-note">记录只保留在当前浏览器。移除记录不会取消后台任务；清除浏览器数据后将无法恢复这些任务。</p>
  </section>;
}
