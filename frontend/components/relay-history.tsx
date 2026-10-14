"use client";

import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  BarChart3,
  CheckCircle2,
  Clock3,
  RefreshCw,
  RotateCcw,
  Send,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { toast } from "sonner";

import { Account, api, post, Task } from "@/lib/api";
import { pollVisible } from "@/lib/polling";
import { Button } from "./ui/button";

type Status = "sent" | "failed" | "sending";
type Entry = {
  destination: string;
  fingerprint: string;
  action: string;
  source_chat: string;
  source_message: number | null;
  status: Status;
  attempts: number;
  created_at: number;
  updated_at: number;
  last_error: string | null;
  account_id: number | null;
  task_id: number | null;
  rule_id: string | null;
  dedup: string | null;
  retryable: boolean;
};
type History = {
  total: number;
  page: number;
  page_size: number;
  items: Entry[];
};
type Stats = {
  total: number;
  sent: number;
  failed: number;
  sending: number;
  last_24h: number;
  success_rate: number | null;
  days: { date: string; sent: number; failed: number; sending: number }[];
};

const emptyHistory: History = { total: 0, page: 1, page_size: 20, items: [] };
const labelStatus: Record<Status, string> = {
  sent: "已发送",
  failed: "发送失败",
  sending: "发送中",
};
const statusClass: Record<Status, string> = {
  sent: "bg-emerald-50 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300",
  failed: "bg-red-50 text-red-700 dark:bg-red-950/40 dark:text-red-300",
  sending: "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300",
};

const formatDate = (seconds: number) =>
  new Date(seconds * 1000).toLocaleString("zh-CN", { hour12: false });

const StatCard = ({
  title,
  value,
  icon: Icon,
  note,
}: {
  title: string;
  value: string | number;
  icon: any;
  note?: string;
}) => (
  <div className="card flex flex-col gap-2 p-4">
    <div className="flex items-center justify-between text-xs text-muted">
      <span>{title}</span>
      <Icon size={16} />
    </div>
    <div className="text-2xl font-bold tabular-nums">{value}</div>
    {note && <p className="text-[11px] text-muted">{note}</p>}
  </div>
);

export function RelayHistory({ accounts, tasks }: { accounts: Account[]; tasks: Task[] }) {
  const [status, setStatus] = useState<"" | Status>("");
  const [accountId, setAccountId] = useState("");
  const [taskId, setTaskId] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [reload, setReload] = useState(0);
  const [history, setHistory] = useState<History>(emptyHistory);
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [statsError, setStatsError] = useState("");
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const selectedTasks = useMemo(
    () => tasks.filter(
      (task) => task.kind === "automation" &&
        (!accountId || task.account_id === Number(accountId))
    ),
    [tasks, accountId],
  );

  useEffect(() => {
    return pollVisible(async (signal) => {
      setLoading(true);
      const common = new URLSearchParams();
      if (accountId) common.set("account_id", accountId);
      if (taskId) common.set("task_id", taskId);
      const filters = new URLSearchParams(common);
      filters.set("page", String(page));
      filters.set("page_size", String(pageSize));
      if (status) filters.set("status", status);
      try {
        const hist = await api<History>("/relay/history?" + filters.toString(), { signal });
        if (!signal.aborted) {
          const pages = Math.max(1, Math.ceil(hist.total / hist.page_size));
          if (page > pages) { setPage(pages); return; }
          setHistory(hist);
          setLastUpdated(new Date());
          setError("");
        }
      } catch (err) {
        if (!signal.aborted) setError((err as Error).message);
      } finally {
        if (!signal.aborted) setLoading(false);
      }
    }, 20000);
  }, [status, accountId, taskId, page, pageSize, reload]);

  useEffect(() => pollVisible(async (signal) => {
    const filters = new URLSearchParams();
    if (accountId) filters.set("account_id", accountId);
    if (taskId) filters.set("task_id", taskId);
    try {
      const result = await api<Stats>("/relay/stats?" + filters, { signal });
      if (!signal.aborted) { setStats(result); setStatsError(""); }
    } catch (err) {
      if (!signal.aborted) setStatsError((err as Error).message);
    }
  }, 60000), [accountId, taskId, reload]);

  const requestRetry = async (entry: Entry) => {
    if (!entry.retryable || busy) return;
    if (!window.confirm(
      `确定重新发送到 ${entry.destination} 吗？\n\n系统会重新读取原消息并核对当前规则。已成功发送或原消息已变化的记录不会重发。`
    )) return;
    const key = entry.destination + entry.fingerprint;
    setBusy(key);
    try {
      const result = await post<{ status: Status }>("/relay/retry", {
        destination: entry.destination,
        fingerprint: entry.fingerprint,
      });
      if (result.status === "sent") {
        toast.success("重试发送成功");
      } else {
        toast.error("重试未成功，请查看最新错误状态");
      }
      setReload((n) => n + 1);
    } catch (err) {
      toast.error((err as Error).message);
      setReload((n) => n + 1);
    } finally {
      setBusy("");
    }
  };

  const totalPages = Math.max(1, Math.ceil(history.total / history.page_size));
  const nameForTask = (id: number | null) =>
    id == null ? "旧记录" : tasks.find((task) => task.id === id)?.name || `已删除任务 #${id}`;
  const nameForAccount = (id: number | null) =>
    id == null ? "未关联" : accounts.find((a) => a.id === id)?.name || `账号 #${id}`;

  return (
    <div className="space-y-5">
      <p role="status" className="text-xs text-muted">
        {loading ? "正在同步记录…" : lastUpdated ? `记录更新于 ${lastUpdated.toLocaleTimeString("zh-CN", { hour12: false })}` : "等待同步…"} · 记录每 20 秒、统计每分钟刷新 · 后台标签页暂停
      </p>
      {statsError && <p role="alert" className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800">统计暂未更新：{statsError}</p>}
      <section className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <StatCard title="总投递记录" value={stats?.total ?? "—"} icon={Send} note="按保留的历史统计，已清理记录不计入" />
        <StatCard title="成功投递" value={stats?.sent ?? "—"} icon={CheckCircle2} />
        <StatCard title="失败投递" value={stats?.failed ?? "—"} icon={AlertCircle} />
        <StatCard title="发送中" value={stats?.sending ?? "—"} icon={Clock3} note="中断的发送可能暂时显示为发送中" />
        <StatCard title="最终成功率" value={stats?.success_rate == null ? "—" : `${stats.success_rate}%`} icon={BarChart3} note={`最近 24 小时有 ${stats?.last_24h ?? "—"} 条记录更新`} />
      </section>

      <section className="card p-5">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold">最近 7 天投递结果</h3>
            <p className="mt-1 text-xs text-muted">按每条记录最近更新日期统计，重试成功会更新该记录状态。</p>
          </div>
          <Button size="sm" variant="outline" disabled={loading} onClick={() => setReload((n) => n + 1)}>
            <RefreshCw size={14} /> 刷新
          </Button>
        </div>
        <div className="h-[180px] w-full">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={stats?.days || []} margin={{ top: 5, right: 8, left: -20, bottom: 0 }}>
              <CartesianGrid vertical={false} strokeDasharray="3 4" />
              <XAxis dataKey="date" tickFormatter={(value) => value.slice(5)} fontSize={11} tickLine={false} />
              <YAxis allowDecimals={false} fontSize={11} tickLine={false} />
              <Tooltip />
              <Bar dataKey="sent" name="成功" stackId="delivery" fill="#0da585" radius={[2, 2, 0, 0]} />
              <Bar dataKey="failed" name="失败" stackId="delivery" fill="#ed9869" />
              <Bar dataKey="sending" name="发送中" stackId="delivery" fill="#80a8ce" />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </section>

      <section className="card overflow-hidden">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line p-5">
          <div>
            <h3 className="text-sm font-semibold">转发历史</h3>
            <p className="mt-1 text-xs text-muted">仅记录投递元数据，不保存消息正文。旧记录仍可查看，但无法直接重试。</p>
          </div>
          <span className="rounded bg-slate-100 px-2 py-1 text-xs text-muted dark:bg-slate-800">{history.total} 条记录</span>
        </div>

        <div className="flex flex-wrap gap-3 px-5 py-4">
          <label className="min-w-[130px] flex-1 text-xs text-muted">
            发送状态
            <select className="mt-1 w-full" value={status} onChange={(e) => { setStatus(e.target.value as "" | Status); setPage(1); }}>
              <option value="">全部状态</option>
              <option value="sent">已发送</option>
              <option value="failed">发送失败</option>
              <option value="sending">发送中</option>
            </select>
          </label>
          <label className="min-w-[130px] flex-1 text-xs text-muted">
            Telegram 账号
            <select className="mt-1 w-full" value={accountId} onChange={(e) => { setAccountId(e.target.value); setTaskId(""); setPage(1); }}>
              <option value="">全部账号</option>
              {accounts.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          </label>
          <label className="min-w-[130px] flex-1 text-xs text-muted">
            自动化任务
            <select className="mt-1 w-full" value={taskId} onChange={(e) => { setTaskId(e.target.value); setPage(1); }}>
              <option value="">全部任务</option>
              {selectedTasks.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </label>
          <Button variant="outline" size="sm" className="self-end" disabled={!status && !accountId && !taskId} onClick={() => { setStatus(""); setAccountId(""); setTaskId(""); setPage(1); }}>重置筛选</Button>
        </div>
        {error && <p className="mx-5 mb-3 rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</p>}
        <div className="overflow-x-auto">
          <table className="w-full min-w-[850px] text-left text-xs">
            <thead className="bg-slate-50 text-muted dark:bg-slate-800/60">
              <tr>
                <th className="px-4 py-3 font-medium">最近更新时间</th>
                <th className="px-4 py-3 font-medium">账号 / 任务</th>
                <th className="px-4 py-3 font-medium">来源消息</th>
                <th className="px-4 py-3 font-medium">转发目标 / 模式</th>
                <th className="px-4 py-3 font-medium">状态</th>
                <th className="px-4 py-3 font-medium">尝试次数</th>
                <th className="px-4 py-3 font-medium">错误类型</th>
                <th className="px-4 py-3 font-medium text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {history.items.map((entry) => {
                const key = entry.destination + entry.fingerprint;
                return (
                  <tr key={key} className="border-t border-line align-top">
                    <td className="whitespace-nowrap px-4 py-3 text-muted">{formatDate(entry.updated_at)}</td>
                    <td className="px-4 py-3">
                      <p className="font-medium">{nameForTask(entry.task_id)}</p>
                      <p className="mt-1 text-muted">{nameForAccount(entry.account_id)}</p>
                    </td>
                    <td className="px-4 py-3">
                      <p className="font-mono">{entry.source_chat}</p>
                      <p className="mt-1 text-muted">消息 #{entry.source_message ?? "—"}</p>
                    </td>
                    <td className="px-4 py-3">
                      <p className="break-all font-medium">{entry.destination}</p>
                      <p className="mt-1 text-muted">{entry.action}</p>
                    </td>
                    <td className="px-4 py-3">
                      <span className={`rounded-md px-2 py-1 ${statusClass[entry.status]}`}>{labelStatus[entry.status]}</span>
                    </td>
                    <td className="px-4 py-3 tabular-nums">{entry.attempts}</td>
                    <td className="px-4 py-3 text-muted">{entry.last_error || "—"}</td>
                    <td className="whitespace-nowrap px-4 py-3 text-right">
                      {entry.retryable ? (
                        <Button size="sm" variant="outline" disabled={Boolean(busy)} onClick={() => void requestRetry(entry)}>
                          <RotateCcw size={13} /> {busy === key ? "重试中…" : "重试"}
                        </Button>
                      ) : (
                        <span className="text-muted">{entry.status === "failed" ? "无法安全重试" : "—"}</span>
                      )}
                    </td>
                  </tr>
                );
              })}
              {history.items.length === 0 && (
                <tr><td colSpan={8} className="px-5 py-12 text-center text-sm text-muted">
                  {loading ? "正在加载投递记录…" : "暂无符合筛选条件的投递记录"}
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line px-5 py-4 text-xs text-muted">
          <span>第 {page} / {totalPages} 页</span>
          <label className="flex items-center gap-2">每页条数<select className="!w-20" value={pageSize} onChange={e => { setPageSize(Number(e.target.value)); setPage(1); }}>{[20, 50, 100].map(size => <option key={size} value={size}>{size}</option>)}</select></label>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" disabled={page <= 1 || loading} onClick={() => setPage((x) => Math.max(1, x - 1))}>
              上一页
            </Button>
            <Button size="sm" variant="outline" disabled={page >= totalPages || loading} onClick={() => setPage((x) => x + 1)}>
              下一页
            </Button>
          </div>
        </div>
      </section>
      <p className="px-1 text-xs leading-6 text-muted">
        失败重试会重新获取 Telegram 来源消息、校验当前任务和规则，且仅允许重试从未成功的记录。
        如果原消息已被删除、编辑或来源/目标设置发生变化，系统会拒绝发送。
        Telegram 接受发送不代表外部转存 Bot 已处理成功。
      </p>
    </div>
  );
}
