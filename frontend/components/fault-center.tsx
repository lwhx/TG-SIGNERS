"use client";

import { useEffect, useState } from "react";
import { AlertTriangle, Download, RefreshCw, Radio, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { api, Account, Task } from "@/lib/api";
import { pollVisible } from "@/lib/polling";
import { Button } from "./ui/button";

type Snapshot = {
  observed_at: number;
  faults: {
    sent: number; failed: number; uncertain: number; sending: number; total: number;
    groups: Array<{account_id: number|null; task_id: number|null; status: string; failure_kind: string; last_error: string; total: number; last_at: number}>;
  };
  retries: {enabled: boolean; due: number; scheduled: number; claimed: number; paused: number; manual_only: number; blocked: number; exhausted: number; expired: number; next_at: number|null};
  listeners: Array<{task_id: number; task_name: string; account_id: number; kind: string; state: string; enabled: boolean; last_status: string; last_run: string|null; last_event: string|null}>;
};
const listenerNames: Record<string, string> = {
  listening: "正在监听", starting: "启动中", restarting: "等待重启",
  disconnected: "连接异常", offline: "未运行", disabled: "已停用",
};

export function FaultCenter({accounts, tasks, onHistory}: {
  accounts: Account[]; tasks: Task[]; onHistory: () => void;
}) {
  const [snapshot, setSnapshot] = useState<Snapshot|null>(null);
  const [accountId, setAccountId] = useState("");
  const [taskId, setTaskId] = useState("");
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [exporting, setExporting] = useState(false);
  const selected = tasks.filter(t => !accountId || t.account_id === Number(accountId));
  const query = new URLSearchParams();
  if (accountId) query.set("account_id", accountId);
  if (taskId) query.set("task_id", taskId);
  const queryString = query.toString();
  useEffect(() => pollVisible(async signal => {
    try {
      const data = await api<Snapshot>("/relay/operations?" + queryString, {signal});
      if (!signal.aborted) { setSnapshot(data); setError(""); }
    } catch (err) {
      if (!signal.aborted) setError((err as Error).message);
    }
  }, 20000), [queryString, revision]);

  const exportCsv = async () => {
    if (exporting) return;
    setExporting(true);
    try {
      const params = new URLSearchParams(queryString);
      params.set("limit", "10000");
      const response = await fetch("/api/relay/export?" + params.toString(), {credentials: "same-origin"});
      if (!response.ok) throw new Error("导出失败，请检查登录状态与服务日志");
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = "relay-records.csv";
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      toast.success("转发记录 CSV 已生成（仅包含元数据）");
    } catch (err) { toast.error((err as Error).message); }
    finally { setExporting(false); }
  };
  const names = (id: number|null, kind: "task"|"account") => {
    if (id == null) return "旧记录";
    return kind === "task"
      ? tasks.find(t => t.id === id)?.name || "任务 #" + id
      : accounts.find(a => a.id === id)?.name || "账号 #" + id;
  };
  return <div className="space-y-5">
    <section className="card space-y-4 p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div><h2 className="font-semibold">故障中心</h2><p className="text-xs text-muted">基于持久投递账本；仅展示元数据，不会自动确认或重发消息。</p></div>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={() => setRevision(x => x + 1)}><RefreshCw size={14}/>刷新</Button>
          <Button size="sm" variant="outline" onClick={() => void exportCsv()} disabled={exporting}><Download size={14}/>导出 CSV</Button>
          <Button size="sm" onClick={onHistory}>查看投递记录</Button>
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-muted">Telegram 账号
          <select className="mt-1 w-full" aria-label="故障中心账号" value={accountId} onChange={e => {setAccountId(e.target.value);setTaskId("");}}>
            <option value="">全部账号</option>{accounts.map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </label>
        <label className="text-xs text-muted">任务
          <select className="mt-1 w-full" aria-label="故障中心任务" value={taskId} onChange={e => setTaskId(e.target.value)}>
            <option value="">全部任务</option>{selected.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
          </select>
        </label>
      </div>
      {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
      {!snapshot && !error && <p role="status" className="text-sm text-muted">正在读取运行状态…</p>}
      {snapshot && <>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {([["未解决故障",snapshot.faults.total],["发送失败",snapshot.faults.failed],["待人工核查",snapshot.faults.uncertain],["发送中",snapshot.faults.sending]] as const).map(([name,count]) =>
            <div key={name} className="rounded-lg border border-line p-4"><div className="text-xs text-muted">{name}</div><div className="mt-2 text-2xl font-bold tabular-nums">{count}</div></div>)}
        </div>
        <p className="text-xs text-muted">更新于 {new Date(snapshot.observed_at*1000).toLocaleString("zh-CN")}；仅统计尚保留的记录。</p>
      </>}
    </section>
    {snapshot && <>
      <section className="card space-y-3 p-5">
        <h3 className="flex items-center gap-2 font-semibold"><RotateCcw size={17}/>重试概览</h3>
        <p className="text-xs text-muted">自动重试：{snapshot.retries.enabled?"已启用":"已关闭"}。只允许 P4 定义的明确临时拒绝进入自动队列；待核查消息禁止自动重发。</p>
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {([["待执行",snapshot.retries.scheduled],["正在处理",snapshot.retries.claimed],["已暂停",snapshot.retries.paused],["可领取",snapshot.retries.due],["仅人工",snapshot.retries.manual_only],["已阻止",snapshot.retries.blocked],["次数用尽",snapshot.retries.exhausted],["已过期",snapshot.retries.expired]] as const).map(([name,count]) =>
            <div key={name} className="rounded-md border border-line p-3"><p className="text-xs text-muted">{name}</p><p className="mt-1 text-xl font-semibold tabular-nums">{count}</p></div>)}
        </div>
        {snapshot.retries.next_at && <p className="text-xs text-muted">最早排期：{new Date(snapshot.retries.next_at*1000).toLocaleString("zh-CN")}</p>}
      </section>
      <section className="card space-y-3 p-5">
        <h3 className="flex items-center gap-2 font-semibold"><Radio size={17}/>监听状态</h3>
        <p className="text-xs text-muted">“正在监听”要求工作进程存活、监听器已注册且账号已连接；不代表每条消息都已被 Telegram 成功推送。</p>
        {snapshot.listeners.length===0 ? <p className="text-sm text-muted">没有符合条件的监听任务。</p> :
          <div className="overflow-x-auto"><table className="w-full min-w-[580px] text-left text-sm">
            <thead><tr className="border-b border-line text-xs text-muted"><th className="py-2">任务</th><th>账号</th><th>类型</th><th>状态</th><th>最近启动</th></tr></thead>
            <tbody>{snapshot.listeners.map(item => <tr key={item.task_id} className="border-b border-line">
              <td className="py-3 font-medium">{item.task_name}</td><td>{names(item.account_id,"account")}</td>
              <td>{item.kind==="monitor"?"消息监控":"自动化"}</td><td>{listenerNames[item.state]||item.state}</td>
              <td className="text-xs text-muted">{item.last_run?new Date(item.last_run).toLocaleString("zh-CN"):"—"}</td></tr>)}</tbody>
          </table></div>}
      </section>
      <section className="card space-y-3 p-5">
        <h3 className="flex items-center gap-2 font-semibold"><AlertTriangle size={17}/>最近故障分组</h3>
        <p className="text-xs text-muted">仅显示最近 25 个错误分组；需确认目标聊天实际结果后才能解除待核查状态。</p>
        {snapshot.faults.groups.length===0?<p className="text-sm text-muted">没有待处理的投递异常。</p>:
        <div className="overflow-x-auto"><table className="w-full min-w-[680px] text-left text-sm">
          <thead><tr className="border-b border-line text-xs text-muted"><th className="py-2">账号 / 任务</th><th>状态</th><th>错误分类</th><th>错误摘要</th><th>数量</th></tr></thead>
          <tbody>{snapshot.faults.groups.map((item,i)=><tr key={i} className="border-b border-line">
            <td className="py-3">{names(item.account_id,"account")} / {names(item.task_id,"task")}</td>
            <td>{item.status==="uncertain"?"待核查":"失败"}</td><td>{item.failure_kind}</td>
            <td className="max-w-[240px] truncate" title={item.last_error}>{item.last_error}</td>
            <td>{item.total}</td></tr>)}</tbody>
        </table></div>}
      </section>
    </>}
  </div>;
}
