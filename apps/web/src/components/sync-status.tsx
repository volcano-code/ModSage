"use client";

import { formatDate } from "@/lib/api";
import { useRemote } from "@/lib/use-remote";
import type { SyncStatus as SyncStatusData } from "@/lib/types";
import { Icon } from "./icons";

const jobLabels: Record<NonNullable<SyncStatusData["latest_job"]>["status"], string> = {
  queued: "等待收录", running: "正在收录", retry_wait: "等待再次尝试",
  succeeded: "已完成", partial: "部分完成", failed: "未能完成",
};
const workerLabels: Record<SyncStatusData["worker"]["status"], string> = {
  recently_seen: "近期收到任务执行响应", stopped: "采集进程已停止",
  stale: "近期未收到任务执行响应", unknown: "暂无任务执行记录",
};

export function SyncStatus() {
  const { data } = useRemote<SyncStatusData>("/sync/status");
  // Refresh status is supplementary: a failed status request must not hide the archive.
  if (!data) return null;
  const job = data.latest_job;
  const incomplete = job?.status === "partial" || job?.status === "failed";

  return <details className={`sync-status ${incomplete ? "sync-incomplete" : ""}`}>
    <summary><span><Icon name={incomplete ? "alert" : "scroll"}/><strong>军报收录</strong><span>{data.schedule.enabled ? `每日 ${data.schedule.time} 编报计划已开启` : "每日编报计划未开启"}</span></span><span>{incomplete ? `最近任务${jobLabels[job.status]}` : "查看收录进度"}<Icon name="chevron"/></span></summary>
    <div className="sync-status-content">
      <dl><div><dt>最近完整更新</dt><dd>{data.last_successful_refresh_at ? formatDate(data.last_successful_refresh_at, true) : "尚无完整更新记录"}</dd></div><div><dt>最近收录任务</dt><dd>{job ? jobLabels[job.status] : "尚无任务记录"}</dd></div><div><dt>下一次计划</dt><dd>{data.schedule.enabled && data.schedule.next_run_at ? formatDate(data.schedule.next_run_at, true) : "尚未安排"}</dd></div><div><dt>任务响应</dt><dd>{workerLabels[data.worker.status]}</dd></div></dl>
      <p>时间均为北京时间。计划开启后仍需采集进程执行；当前展示最近一次已生成的战报。</p>
    </div>
  </details>;
}
