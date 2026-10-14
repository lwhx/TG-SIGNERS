"use client";
import { useEffect, useState } from "react";
import {
  Activity,
  ArrowDownToLine,
  ArrowRight,
  ArrowUpRight,
  Bell,
  Bot,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleHelp,
  Clock3,
  Code2,
  Download,
  Edit3,
  ExternalLink,
  Github,
  LayoutDashboard,
  Loader2,
  LogOut,
  Menu,
  Moon,
  MoreHorizontal,
  Play,
  Plus,
  Radio,
  RefreshCw,
  Search,
  Send,
  Settings2,
  ShieldCheck,
  Square,
  Sun,
  TerminalSquare,
  Trash2,
  Users,
  Workflow,
  Wrench,
  X,
  Zap,
} from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { ColumnDef } from "@tanstack/react-table";
import { Toaster, toast } from "sonner";
import { Account, api, post, Run, Task } from "@/lib/api";
import { Page, useUI } from "@/lib/store";
import { cn, date } from "@/lib/utils";
import { describeSchedule } from "@/lib/schedule";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogTrigger,
  DialogClose,
} from "@/components/ui/dialog";
import { Switch } from "@/components/ui/switch";
import {
  ActionButton,
  DataTable,
  Empty,
  Field,
  Status,
} from "@/components/common";
import { AuthDialog } from "@/components/auth-dialog";
import { TaskEditor } from "@/components/task-editor";
import { Settings } from "@/components/settings";
import { Tools } from "@/components/tools";
import { Logs } from "@/components/logs";
import { RelayHistory } from "@/components/relay-history";
const navigation: [Page, string, any][] = [
  ["overview", "概览", LayoutDashboard],
  ["accounts", "Telegram 账号", Users],
  ["tasks", "签到任务", CheckCircle2],
  ["automation", "自动化与转发", Workflow],
  ["relay", "转发记录", Activity],
  ["logs", "日志与终端", TerminalSquare],
  ["tools", "工具箱", Wrench],
  ["settings", "系统设置", Settings2],
];
const descriptions: Record<Page, string> = {
  overview: "所有账号与自动化任务，一目了然。",
  accounts: "安全连接您的 Telegram 账号，统一管理每一个会话。",
  tasks: "配置签到动作与时间计划，让日常任务自动完成。",
  automation: "监听多个来源，对消息进行筛选、提取、去重并转发给频道、群组或 Bot。",
  relay: "查看 Telegram 投递状态、失败原因和最近 7 天统计，并安全重试失败消息。",
  logs: "实时追踪任务执行过程，查看每一次 Telegram 回复。",
  tools: "发现对话、管理消息，以及使用 tg-signer 的进阶能力。",
  settings: "让网络、通知与安全配置适合您的工作方式。",
};
export default function Home() {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  const [darkMode, setDarkMode] = useState(false);
  const { page, setPage, revision, refresh, setAuthOpen } = useUI();
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [overview, setOverview] = useState<any>(null);
  const [error, setError] = useState("");
  const [sidebar, setSidebar] = useState(false);
  const [editor, setEditor] = useState<{
    kind: Task["kind"];
    task?: Task;
  } | null>(null);
  const [accountEdit, setAccountEdit] = useState<Account | null>(null);
  const [batch, setBatch] = useState<Task | null>(null);
  const [selectedAccounts, setSelectedAccounts] = useState<number[]>([]);
  const [search, setSearch] = useState("");
  const taskSearch = search.trim().toLowerCase();
  useEffect(() => {
    const stored = window.localStorage.getItem("tg-signer-theme");
    const enabled = stored
      ? stored === "dark"
      : window.matchMedia("(prefers-color-scheme: dark)").matches;
    setDarkMode(enabled);
    document.documentElement.classList.toggle("dark", enabled);
  }, []);
  const toggleTheme = () => {
    const enabled = !darkMode;
    setDarkMode(enabled);
    document.documentElement.classList.toggle("dark", enabled);
    window.localStorage.setItem("tg-signer-theme", enabled ? "dark" : "light");
  };
  useEffect(() => {
    api("/panel/me")
      .then(() => setAuthenticated(true))
      .catch(() => setAuthenticated(false));
    const expired = () => setAuthenticated(false);
    window.addEventListener("session-expired", expired);
    return () => window.removeEventListener("session-expired", expired);
  }, []);
  useEffect(() => {
    if (!authenticated) return;
    let alive = true;
    const load = async () => {
      try {
        const [a, t, o] = await Promise.all([
          api<Account[]>("/accounts"),
          api<Task[]>("/tasks"),
          api("/overview"),
        ]);
        if (alive) {
          setAccounts(a);
          setTasks(t);
          setOverview(o);
          setError("");
        }
      } catch (e) {
        if (alive) setError((e as Error).message);
      }
    };
    void load();
    const timer = setInterval(load, 15000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [authenticated, revision]);
  useEffect(() => setSearch(""), [page]);
  const newTask = (kind: Task["kind"] = "signer") => {
    if (!accounts.length) {
      toast.info("请先连接一个 Telegram 账号");
      setAuthOpen(true);
    } else setEditor({ kind });
  };
  const run = (path: string) => async () => {
    await post(path);
    refresh();
  };
  const accountName = (id: number) =>
    accounts.find((a) => a.id === id)?.name || "未知账号";
  const taskColumns: ColumnDef<Task, any>[] = [
    {
      accessorKey: "name",
      header: "任务名称 / 目标",
      cell: ({ row }) => (
        <div className="flex items-center gap-3">
          <div
            className={cn(
              "rounded-lg p-2",
              row.original.kind === "signer"
                ? "bg-emerald-50 text-primary"
                : "bg-violet-50 text-violet-600",
            )}
          >
            {row.original.kind === "signer" ? (
              <Bot size={18} />
            ) : (
              <Workflow size={18} />
            )}
          </div>
          <div>
            <button
              className="font-medium hover:text-primary"
              onClick={() =>
                setEditor({ kind: row.original.kind, task: row.original })
              }
            >
              {row.original.name}
            </button>
            <p className="mt-1 max-w-48 truncate text-xs text-muted">
              {row.original.kind === "signer"
                ? row.original.config.chats
                    ?.map((c: any) => c.chat_id)
                    .join(", ")
                : row.original.kind === "automation"
                  ? "Automation · 规则引擎"
                  : "Monitor · 消息监控"}
            </p>
          </div>
        </div>
      ),
    },
    {
      accessorKey: "account_id",
      header: "执行账号",
      cell: ({ getValue }) => (
        <span className="text-xs">{accountName(getValue())}</span>
      ),
    },
    {
      accessorKey: "cron",
      header: "执行计划",
      cell: ({ row }) => (
        <div>
          <span className="font-mono text-xs">
            {row.original.kind === "signer"
              ? describeSchedule(row.original.cron)
              : "持续监听"}
          </span>
          <p className="mt-1 text-[11px] text-muted">
            {row.original.kind === "signer"
              ? `延迟 ${row.original.delay_min}–${row.original.delay_max}s · ${row.original.timezone}`
              : `${row.original.kind === "automation" ? (row.original.config.rules?.length || 0) + " 条规则" : "监控消息匹配"}`}
          </p>
        </div>
      ),
    },
    {
      accessorKey: "last_status",
      header: "最近执行",
      cell: ({ row }) => (
        <div>
          <Status
            value={row.original.running ? "running" : row.original.last_status}
          />
          <p className="mt-1 text-[10px] text-muted">
            {date(row.original.last_run)}
          </p>
          {row.original.next_run && row.original.enabled && (
            <p className="mt-1 text-[10px] text-muted">
              下次 {date(row.original.next_run)}
            </p>
          )}
        </div>
      ),
    },
    {
      accessorKey: "enabled",
      header: "启用",
      cell: ({ row }) => (
        <Switch
          label={`启用 ${row.original.name}`}
          checked={row.original.enabled}
          onCheckedChange={async (enabled) => {
            try {
              await api(`/tasks/${row.original.id}`, {
                method: "PUT",
                body: JSON.stringify({ ...row.original, enabled }),
              });
              refresh();
            } catch (e) {
              toast.error((e as Error).message);
            }
          }}
        />
      ),
    },
    {
      id: "actions",
      header: "操作",
      cell: ({ row }) => (
        <div className="flex items-center gap-1">
          <ActionButton
            variant="ghost"
            size="icon"
            title={row.original.running ? "停止任务" : "立即执行"}
            aria-label={row.original.running ? "停止任务" : "立即执行"}
            action={run(
              `/tasks/${row.original.id}/${row.original.running ? "stop" : "run"}`,
            )}
          >
            {row.original.running ? <Square size={14} /> : <Play size={15} />}
          </ActionButton>
          <Button
            variant="ghost"
            size="icon"
            title="编辑任务"
            aria-label="编辑任务"
            onClick={() =>
              setEditor({ kind: row.original.kind, task: row.original })
            }
          >
            <Edit3 size={15} />
          </Button>
          <Dialog>
            <DialogTrigger asChild>
              <Button variant="ghost" size="icon" aria-label="更多任务操作">
                <MoreHorizontal size={17} />
              </Button>
            </DialogTrigger>
            <DialogContent title="任务操作" description={row.original.name}>
              <div className="grid gap-2">
                <a
                  className="block rounded px-3 py-2 text-xs hover:bg-slate-50"
                  href={`/api/tasks/${row.original.id}/export`}
                  download
                >
                  导出 JSON
                </a>
                <a
                  className="block rounded px-3 py-2 text-xs hover:bg-slate-50"
                  href={`/api/tasks/${row.original.id}/export?format=yaml`}
                  download
                >
                  导出 YAML
                </a>
                {row.original.kind === "signer" && (
                  <DialogClose asChild>
                    <button
                      className="block w-full rounded px-3 py-2 text-left text-xs hover:bg-slate-50"
                      onClick={() => {
                        setBatch(row.original);
                        setSelectedAccounts([row.original.account_id]);
                      }}
                    >
                      多账号执行
                    </button>
                  </DialogClose>
                )}
                <ActionButton
                  variant="ghost"
                  className="!h-8 w-full !justify-start !px-3 !text-xs !text-red-500"
                  action={async () => {
                    if (
                      !confirm(
                        `删除任务「${row.original.name}」？执行历史将保留。`,
                      )
                    )
                      return;
                    await api(`/tasks/${row.original.id}`, {
                      method: "DELETE",
                    });
                    refresh();
                  }}
                >
                  删除任务
                </ActionButton>
              </div>
            </DialogContent>
          </Dialog>
        </div>
      ),
    },
  ];
  if (authenticated === null)
    return (
      <div className="flex min-h-screen items-center justify-center">
        <Loader2 className="animate-spin text-primary" />
      </div>
    );
  if (!authenticated)
    return (
      <>
        <Toaster richColors position="top-right" theme={darkMode ? "dark" : "light"} />
        <Login
          onLogin={() => {
            setAuthenticated(true);
            refresh();
          }}
        />
      </>
    );
  return (
    <>
      <Toaster
        richColors
        position="top-right"
        closeButton
        theme={darkMode ? "dark" : "light"}
      />
      <div className="min-h-screen">
        <div
          className={cn(
            "fixed inset-0 z-30 bg-slate-950/40 lg:hidden",
            !sidebar && "hidden",
          )}
          onClick={() => setSidebar(false)}
        />
        <aside
          className={cn(
            "fixed inset-y-0 left-0 z-40 flex w-[230px] flex-col bg-[#122730] text-slate-300 transition-transform lg:translate-x-0",
            sidebar ? "translate-x-0" : "-translate-x-full",
          )}
        >
          <div className="flex h-24 items-center gap-3 px-7">
            <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-[#58ddbe] text-[#103c36]">
              <Send size={21} strokeWidth={2.3} />
            </div>
            <div>
              <p className="text-lg font-semibold tracking-tight text-white">
                tg-signer<span className="text-[#58ddbe]">.</span>
              </p>
              <p className="mt-0.5 text-[9px] tracking-[.15em] text-slate-500">
                AUTOMATION CONSOLE
              </p>
            </div>
          </div>
          <p className="eyebrow px-7 pb-3 pt-3 text-slate-500">工作空间</p>
          <nav className="space-y-1 px-4">
            {navigation.map(([id, label, Icon], i) => (
              <div key={id}>
                {i === 6 && (
                  <div className="mx-3 my-5 border-t border-white/[.07]" />
                )}
                <button
                  aria-label={label}
                  onClick={() => {
                    setPage(id);
                    setSidebar(false);
                  }}
                  className={cn(
                    "flex w-full items-center gap-3 rounded-lg px-3 py-3 text-[13px] transition-colors",
                    page === id
                      ? "bg-[#1d403f] font-medium text-[#71e4c6]"
                      : "text-[#a3b4bb] hover:bg-white/5 hover:text-white",
                  )}
                >
                  <Icon size={18} strokeWidth={1.7} />
                  {label}
                  {id === "tasks" &&
                    tasks.filter((t) => t.kind === "signer").length > 0 && (
                      <span className="ml-auto rounded bg-white/10 px-1.5 py-0.5 text-[10px]">
                        {tasks.filter((t) => t.kind === "signer").length}
                      </span>
                    )}
                  {page === id && (
                    <span className="ml-auto h-1.5 w-1.5 rounded-full bg-[#71e4c6]" />
                  )}
                </button>
              </div>
            ))}
          </nav>
          <div className="mt-auto p-5">
            <div className="rounded-xl border border-white/[.07] bg-white/[.03] p-4">
              <div className="mb-3 flex items-center gap-2 text-xs font-medium text-[#8ae5ce]">
                <span className="h-1.5 w-1.5 rounded-full bg-[#68d8b9]" />
                自动化，让生活简单一点
              </div>
              <p className="text-[11px] leading-6 text-slate-500">
                由开源项目 tg-signer 驱动
                <br />
                每一次签到，都井然有序。
              </p>
              <a
                className="mt-3 flex items-center gap-1.5 text-[11px] text-slate-400 hover:text-white"
                href="https://github.com/amchii/tg-signer"
                target="_blank"
                rel="noreferrer"
              >
                <Github size={13} />
                项目文档
                <ArrowUpRight size={12} />
              </a>
            </div>
            <div className="mt-5 flex items-center gap-2 px-2 text-[10px] text-slate-600">
              <ShieldCheck size={12} />
              本地部署 · 数据由您掌控
            </div>
          </div>
        </aside>
        <div className="lg:ml-[230px]">
          <header className="flex h-[72px] items-center justify-between border-b border-line bg-white px-5 md:px-9">
            <div className="flex items-center gap-3 text-xs">
              <Button
                className="lg:hidden"
                variant="ghost"
                size="icon"
                aria-label="打开导航"
                onClick={() => setSidebar(true)}
              >
                <Menu size={20} />
              </Button>
              <span className="text-muted">工作空间</span>
              <ChevronRight size={13} className="text-slate-300" />
              <span className="font-medium">
                {navigation.find((n) => n[0] === page)?.[1]}
              </span>
            </div>
            <div className="flex items-center gap-4">
              <span
                className={cn(
                  "hidden items-center gap-2 rounded-full px-3 py-1.5 text-[11px] sm:flex",
                  error
                    ? "bg-amber-50 text-amber-700"
                    : "bg-emerald-50 text-primary",
                )}
              >
                <span className="h-1.5 w-1.5 rounded-full bg-current" />
                {error ? "连接异常" : "服务运行正常"}
              </span>
              <button
                className="text-muted hover:text-primary"
                aria-label="刷新页面数据"
                onClick={refresh}
              >
                <RefreshCw size={16} />
              </button>
              <button
                className="text-muted hover:text-primary"
                aria-label={darkMode ? "切换浅色主题" : "切换暗色主题"}
                aria-pressed={darkMode}
                title={darkMode ? "切换浅色主题" : "切换暗色主题"}
                onClick={toggleTheme}
              >
                {darkMode ? <Sun size={16} /> : <Moon size={16} />}
              </button>
              <span className="h-5 w-px bg-line" />
              <div className="flex items-center gap-2">
                <div className="flex h-8 w-8 items-center justify-center rounded-full bg-[#e9efed] text-xs font-semibold text-primary">
                  AD
                </div>
                <span className="hidden text-xs font-medium sm:block">
                  管理员
                </span>
                <ActionButton
                  variant="ghost"
                  size="icon"
                  title="退出面板"
                  aria-label="退出面板"
                  action={async () => {
                    await post("/panel/logout");
                    setAuthenticated(false);
                  }}
                >
                  <LogOut size={15} />
                </ActionButton>
              </div>
            </div>
          </header>
          <main className="mx-auto max-w-[1530px] p-5 md:p-9">
            <div className="mb-8 flex flex-wrap items-center justify-between gap-4">
              <div>
                <div className="mb-2 flex items-center gap-2">
                  <span className="eyebrow text-primary">
                    {page === "overview"
                      ? "YOUR AUTOMATION, AT A GLANCE"
                      : "TG-SIGNER WORKSPACE"}
                  </span>
                  {page === "overview" && (
                    <span className="h-px w-8 bg-emerald-200" />
                  )}
                </div>
                <h1 className="page-title">
                  {page === "overview"
                    ? "工作空间概览"
                    : navigation.find((n) => n[0] === page)?.[1]}
                </h1>
                <p className="page-description">{descriptions[page]}</p>
              </div>
              <div className="flex gap-2">
                {page === "accounts" ? (
                  <Button onClick={() => setAuthOpen(true)}>
                    <Plus size={16} />
                    添加账号
                  </Button>
                ) : ["overview", "tasks", "automation"].includes(page) ? (
                  <>
                    <Button
                      variant="outline"
                      onClick={() => {
                        if (page === "automation") newTask("monitor");
                        else setAuthOpen(true);
                      }}
                    >
                      {page === "automation" ? (
                        <Radio size={15} />
                      ) : (
                        <Users size={15} />
                      )}{" "}
                      {page === "automation" ? "新建消息监控" : "添加账号"}
                    </Button>
                    <Button
                      onClick={() =>
                        newTask(page === "automation" ? "automation" : "signer")
                      }
                    >
                      <Plus size={16} />
                      {page === "automation" ? "新建自动化" : "新建任务"}
                    </Button>
                  </>
                ) : null}
              </div>
            </div>
            {error && (
              <div
                role="alert"
                className="mb-5 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-800"
              >
                {error}{" "}
                <button className="ml-3 underline" onClick={refresh}>
                  重新加载
                </button>
              </div>
            )}
            {page === "overview" && (
              <Overview
                data={overview}
                tasks={tasks}
                accounts={accounts}
                newTask={() => newTask()}
                onEdit={(task) => setEditor({ kind: task.kind, task })}
              />
            )}
            {page === "accounts" && (
              <>
                <div className="mb-5 flex items-center justify-between gap-3">
                  <p className="text-sm text-muted">
                    共 <b className="text-ink">{accounts.length}</b> 个账号 ·
                    状态以最近验证结果为准
                  </p>
                  <ActionButton
                    variant="outline"
                    action={async () => {
                      const results = await post("/accounts/sync");
                      refresh();
                      if (results.some((r: any) => r.status === "error"))
                        throw new Error("部分账号验证失败，请查看账号状态");
                    }}
                    success="会话已同步"
                  >
                    <RefreshCw size={15} />
                    同步全部会话
                  </ActionButton>
                </div>
                {!accounts.length ? (
                  <div className="card">
                    <Empty
                      title="连接您的第一个 Telegram 账号"
                      description="使用手机号登录或导入已有 Session，开始配置每日签到与自动化。"
                      action={
                        <Button
                          className="mt-3"
                          onClick={() => setAuthOpen(true)}
                        >
                          <Plus size={15} />
                          添加账号
                        </Button>
                      }
                    />
                  </div>
                ) : (
                  <div className="grid gap-5 md:grid-cols-2 2xl:grid-cols-3">
                    {accounts.map((a) => (
                      <section key={a.id} className="card p-5">
                        <div className="flex items-start justify-between">
                          <div className="flex items-center gap-3">
                            {a.avatar ? (
                              <img
                                src={a.avatar}
                                alt={a.name}
                                className="h-12 w-12 rounded-full"
                              />
                            ) : (
                              <div className="flex h-12 w-12 items-center justify-center rounded-full bg-emerald-100 text-lg font-semibold text-primary">
                                {a.name.slice(0, 1).toUpperCase()}
                              </div>
                            )}
                            <div>
                              <h2 className="font-semibold">{a.name}</h2>
                              <p className="mt-1 text-xs text-muted">
                                {a.username ? "@" + a.username : a.phone}
                              </p>
                            </div>
                          </div>
                          <Status value={a.status} />
                        </div>
                        <dl className="my-5 grid grid-cols-[64px_1fr] gap-y-3 text-xs">
                          <dt className="text-muted">手机号</dt>
                          <dd>{a.phone}</dd>
                          <dt className="text-muted">用户 ID</dt>
                          <dd className="font-mono">{a.user_id}</dd>
                          <dt className="text-muted">连接代理</dt>
                          <dd className="truncate">
                            {a.proxy || "使用全局设置"}
                          </dd>
                          <dt className="text-muted">最近验证</dt>
                          <dd>
                            {a.last_checked ? date(a.last_checked) : "尚未验证"}
                          </dd>
                        </dl>
                        <div className="flex flex-wrap items-center gap-2 border-t border-line pt-4">
                          <ActionButton
                            variant="outline"
                            size="sm"
                            action={run(`/accounts/${a.id}/ping`)}
                            success="Telegram 连接正常"
                          >
                            <Activity size={13} />
                            测试连接
                          </ActionButton>
                          <ActionButton
                            variant="ghost"
                            size="icon"
                            title="刷新 Session"
                            aria-label="刷新 Session"
                            action={run(`/accounts/${a.id}/refresh`)}
                            success="会话已刷新"
                          >
                            <RefreshCw size={14} />
                          </ActionButton>
                          <Button
                            variant="ghost"
                            size="icon"
                            title="编辑账号"
                            aria-label="编辑账号"
                            onClick={() => setAccountEdit(a)}
                          >
                            <Edit3 size={14} />
                          </Button>
                          <Button
                            asChild
                            variant="ghost"
                            size="icon"
                            title="导出 Session"
                          >
                            <a
                              aria-label="导出 Session"
                              href={`/api/accounts/${a.id}/export`}
                              download
                            >
                              <Download size={14} />
                            </a>
                          </Button>
                          <ActionButton
                            variant="ghost"
                            size="icon"
                            title="删除账号"
                            aria-label="删除账号"
                            className="ml-auto text-red-400"
                            action={async () => {
                              if (
                                !confirm(
                                  `删除本地账号「${a.name}」？这不会注销 Telegram。`,
                                )
                              )
                                return;
                              await api(`/accounts/${a.id}`, {
                                method: "DELETE",
                              });
                              refresh();
                            }}
                          >
                            <Trash2 size={14} />
                          </ActionButton>
                        </div>
                      </section>
                    ))}
                  </div>
                )}
              </>
            )}
            {(page === "tasks" || page === "automation") && (
              <section className="card overflow-visible">
                <div className="flex flex-wrap items-center justify-between gap-3 p-5">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold">
                      {page === "tasks" ? "全部签到任务" : "规则与监控"}
                    </span>
                    <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-muted">
                      {
                        tasks.filter((t) =>
                          page === "tasks"
                            ? t.kind === "signer"
                            : t.kind !== "signer",
                        ).length
                      }
                    </span>
                  </div>
                  <div className="flex w-full flex-wrap gap-3 sm:w-auto">
                    <div className="relative">
                      <Search
                        size={15}
                        className="absolute left-3 top-3 text-muted"
                      />
                      <input
                        className="!py-2 !pl-9"
                        aria-label="搜索任务"
                        placeholder={
                          page === "tasks"
                            ? "搜索任务名称、Bot 名称或 Chat ID…"
                            : "搜索任务…"
                        }
                        title={
                          page === "tasks"
                            ? "支持任务名称、Bot 用户名（如 @YourBot）和 Chat ID"
                            : "搜索任务名称"
                        }
                        value={search}
                        onChange={(e) => setSearch(e.target.value)}
                      />
                    </div>
                    {page === "tasks" && (
                      <ActionButton
                        variant="outline"
                        size="sm"
                        className="!h-9"
                        disabled={
                          !tasks.some((t) => t.enabled && t.kind === "signer")
                        }
                        action={run("/tasks/run-all")}
                        success="已加入执行队列"
                      >
                        <Play size={13} />
                        全部执行
                      </ActionButton>
                    )}
                  </div>
                </div>
                <DataTable
                  key={page}
                  resetPageKey={taskSearch}
                  data={tasks.filter(
                    (t) =>
                      (page === "tasks"
                        ? t.kind === "signer"
                        : t.kind !== "signer") &&
                      (t.name.toLowerCase().includes(taskSearch) ||
                        (t.kind === "signer" &&
                          t.config.chats?.some(
                            (chat: { chat_id?: string | number; name?: string }) =>
                              [chat.chat_id, chat.name].some((value) =>
                                String(value ?? "")
                                  .toLowerCase()
                                  .includes(taskSearch),
                              ),
                          ))),
                  )}
                  columns={taskColumns}
                  empty={
                    page === "tasks" ? "还没有签到任务" : "还没有自动化规则"
                  }
                />
              </section>
            )}
            {page === "relay" && <RelayHistory accounts={accounts} tasks={tasks} />}
            {page === "logs" && <Logs accounts={accounts} tasks={tasks} />}{" "}
            {page === "tools" && <Tools accounts={accounts} />}{" "}
            {page === "settings" && <Settings />}
            <footer className="mt-9 flex flex-wrap justify-between gap-2 border-t border-line pt-5 text-[10px] text-muted">
              <span>
                tg-signer Dashboard{" "}
                <span className="mx-2 text-slate-300">/</span>{" "}
                为每一天，省一点时间。
              </span>
              <span>
                {overview?.timezone || "Asia/Shanghai"}{" "}
                <span className="mx-2 text-slate-300">·</span> v1.0.0
              </span>
            </footer>
          </main>
        </div>
      </div>
      <AuthDialog />
      {editor && (
        <TaskEditor
          key={editor.task?.id || editor.kind}
          task={editor.task}
          kind={editor.kind}
          accounts={accounts}
          onClose={() => setEditor(null)}
        />
      )}{" "}
      {accountEdit && (
        <EditAccount
          account={accountEdit}
          onClose={() => setAccountEdit(null)}
        />
      )}
      <Dialog open={!!batch} onOpenChange={(open) => !open && setBatch(null)}>
        <DialogContent
          title="多账号执行"
          description="为其他账号创建禁用的任务副本，并立即执行一次相同配置。"
        >
          <div className="mb-5 space-y-3">
            {accounts.map((a) => (
              <label
                key={a.id}
                className="flex items-center gap-3 rounded-lg border border-line p-3 text-sm"
              >
                <input
                  type="checkbox"
                  checked={selectedAccounts.includes(a.id)}
                  onChange={(e) =>
                    setSelectedAccounts(
                      e.target.checked
                        ? [...selectedAccounts, a.id]
                        : selectedAccounts.filter((id) => id !== a.id),
                    )
                  }
                />
                {a.name}
                <span className="ml-auto text-xs text-muted">{a.phone}</span>
              </label>
            ))}
          </div>
          <ActionButton
            disabled={!selectedAccounts.length}
            className="w-full"
            action={async () => {
              await post(`/tasks/${batch?.id}/multi-run`, selectedAccounts);
              refresh();
              setBatch(null);
            }}
            success="多账号任务已加入执行队列"
          >
            <Play size={15} />
            执行所选账号
          </ActionButton>
        </DialogContent>
      </Dialog>
    </>
  );
}
function Login({ onLogin }: { onLogin: () => void }) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  return (
    <main className="grid min-h-screen lg:grid-cols-2">
      <section className="relative hidden flex-col justify-between overflow-hidden bg-[#122730] p-16 text-white lg:flex">
        <div className="absolute -left-48 top-32 h-[600px] w-[600px] rounded-full border border-white/5" />
        <div className="absolute -left-32 top-48 h-[470px] w-[470px] rounded-full border border-white/5" />
        <div className="relative flex items-center gap-3">
          <div className="rounded-xl bg-[#58ddbe] p-2 text-[#123c36]">
            <Send size={24} />
          </div>
          <span className="text-xl font-semibold">tg-signer.</span>
        </div>
        <div className="relative">
          <p className="eyebrow mb-6 text-[#58ddbe]">
            LESS REPETITION. MORE POSSIBILITY.
          </p>
          <h1 className="text-5xl font-semibold leading-[1.4] tracking-tight">
            让重复的事，
            <br />
            自动发生。
          </h1>
          <p className="mt-6 max-w-sm text-sm leading-8 text-slate-400">
            从每日签到到消息自动化，一个工作空间，连接您的全部 Telegram 任务。
          </p>
          <div className="mt-10 flex gap-6 text-xs text-slate-400">
            <span className="flex items-center gap-2">
              <ShieldCheck size={16} className="text-[#58ddbe]" />
              安全管理
            </span>
            <span className="flex items-center gap-2">
              <Zap size={16} className="text-[#58ddbe]" />
              自动执行
            </span>
            <span className="flex items-center gap-2">
              <Activity size={16} className="text-[#58ddbe]" />
              实时掌控
            </span>
          </div>
        </div>
        <p className="relative text-xs text-slate-600">
          OPEN SOURCE · SELF HOSTED · YOUR DATA
        </p>
      </section>
      <section className="flex items-center justify-center p-8">
        <div className="w-full max-w-sm">
          <div className="mb-7 flex h-12 w-12 items-center justify-center rounded-2xl bg-emerald-100 text-primary">
            <Send size={24} />
          </div>
          <h2 className="text-3xl font-semibold tracking-tight">欢迎回来</h2>
          <p className="mb-8 mt-3 text-sm text-muted">
            登录 tg-signer 管理控制台，开启轻松的一天。
          </p>
          <form
            className="space-y-5"
            onSubmit={async (e) => {
              e.preventDefault();
              setBusy(true);
              setError("");
              try {
                await post("/panel/login", { password });
                onLogin();
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            <Field label="管理密码">
              <input
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                placeholder="输入您的管理密码"
              />
            </Field>
            {error && (
              <p role="alert" className="text-sm text-red-600">
                {error}
              </p>
            )}
            <Button type="submit" className="w-full" disabled={busy}>
              {busy ? (
                <Loader2 className="animate-spin" size={16} />
              ) : (
                <ArrowRight size={16} />
              )}
              登录控制台
            </Button>
          </form>
          <p className="mt-6 text-xs leading-6 text-muted">
            首次部署的随机密码保存在数据目录的
            <br />
            <code className="text-primary">initial-password.txt</code> 文件中。
          </p>
          <p className="mt-14 flex items-center gap-1.5 text-[11px] text-muted">
            <ShieldCheck size={13} />
            您的账号与会话，仅存储于本机。
          </p>
        </div>
      </section>
    </main>
  );
}
function Overview({
  data,
  tasks,
  accounts,
  newTask,
  onEdit,
}: {
  data: any;
  tasks: Task[];
  accounts: Account[];
  newTask: () => void;
  onEdit: (t: Task) => void;
}) {
  const { setPage, setAuthOpen, refresh } = useUI();
  const [clock, setClock] = useState(Date.now());
  useEffect(() => {
    const t = setInterval(() => setClock(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const seconds = data?.next_run
    ? Math.max(
        0,
        Math.floor((new Date(data.next_run).getTime() - clock) / 1000),
      )
    : null;
  const countdown =
    seconds === null
      ? "-- : -- : --"
      : `${String(Math.floor(seconds / 3600)).padStart(2, "0")} : ${String(Math.floor(seconds / 60) % 60).padStart(2, "0")} : ${String(seconds % 60).padStart(2, "0")}`;
  const metrics = [
    {
      label: "已绑定账号",
      value: data?.accounts ?? "—",
      unit: "个",
      icon: Users,
      note: `${accounts.filter((a) => a.status === "online").length} 个最近验证有效`,
      color: "text-primary bg-emerald-50",
    },
    {
      label: "激活任务",
      value: data?.active_tasks ?? "—",
      unit: "个",
      icon: Workflow,
      note: `${data?.running ?? 0} 个任务正在运行`,
      color: "text-blue-600 bg-blue-50",
    },
    {
      label: "今日签到成功率",
      value: data?.success_rate === null || !data ? "—" : data.success_rate,
      unit: "%",
      icon: CheckCircle2,
      note: `今日 ${data?.today_runs ?? 0} 次执行 · 按匹配结果统计`,
      color: "text-amber-600 bg-amber-50",
    },
    {
      label: "距离下一次执行",
      value: countdown,
      unit: "",
      icon: Clock3,
      note: data?.next_run ? date(data.next_run) : "启用任务后显示执行倒计时",
      color: "text-violet-600 bg-violet-50",
    },
  ];
  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {metrics.map((m, i) => (
          <section className="card p-5" key={m.label}>
            <div className="flex items-center justify-between">
              <span className="text-xs font-medium text-muted">{m.label}</span>
              <div className={cn("rounded-lg p-2", m.color)}>
                <m.icon size={17} />
              </div>
            </div>
            <div className="my-4 flex items-baseline gap-2">
              <span
                className={cn(
                  "font-semibold tracking-tight",
                  i === 3 ? "font-mono text-[25px]" : "text-[34px]",
                )}
              >
                {m.value}
              </span>
              <span className="text-xs text-muted">{m.unit}</span>
            </div>
            <p className="text-[11px] text-muted">{m.note}</p>
          </section>
        ))}
      </div>
      <div className="grid gap-6 xl:grid-cols-[1fr_310px]">
        <section className="card p-5 md:p-6">
          <div className="mb-7 flex items-center justify-between">
            <div>
              <h2 className="text-sm font-semibold">任务执行趋势</h2>
              <p className="mt-1.5 text-xs text-muted">
                让每一次自动化，都有迹可循
              </p>
            </div>
            <span className="rounded-md border border-line px-3 py-1.5 text-[11px] text-muted">
              最近 7 天
            </span>
          </div>
          <div className="h-[225px]" style={{ minWidth: 0 }}>
            {data && (
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart
                  data={data.trend}
                  margin={{ left: -25, right: 10, top: 5, bottom: 0 }}
                >
                  <defs>
                    <linearGradient
                      id="successFill"
                      x1="0"
                      y1="0"
                      x2="0"
                      y2="1"
                    >
                      <stop
                        offset="0%"
                        stopColor="#1cb997"
                        stopOpacity={0.18}
                      />
                      <stop offset="100%" stopColor="#1cb997" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid
                    strokeDasharray="4 5"
                    vertical={false}
                    stroke="#edf0f2"
                  />
                  <XAxis
                    dataKey="date"
                    tickFormatter={(v) => v.slice(5)}
                    tick={{ fontSize: 10, fill: "#8b9ba2" }}
                    axisLine={false}
                    tickLine={false}
                    dy={8}
                  />
                  <YAxis
                    allowDecimals={false}
                    domain={[0, (max: number) => Math.max(4, max)]}
                    tick={{ fontSize: 10, fill: "#8b9ba2" }}
                    tickLine={false}
                    axisLine={false}
                  />
                  <Tooltip
                    contentStyle={{
                      fontSize: 12,
                      borderRadius: 10,
                      borderColor: "#e5ebed",
                    }}
                  />
                  <Area
                    type="monotone"
                    name="成功"
                    dataKey="success"
                    stroke="#0da585"
                    strokeWidth={2.3}
                    fill="url(#successFill)"
                  />
                  <Area
                    type="monotone"
                    name="失败"
                    dataKey="failed"
                    stroke="#ed9869"
                    strokeWidth={2}
                    fill="transparent"
                  />
                  <Area
                    type="monotone"
                    name="已执行 / 未验证"
                    dataKey="completed"
                    stroke="#80a8ce"
                    strokeWidth={1.5}
                    fill="transparent"
                  />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>
          <div className="mt-5 flex justify-center gap-5 text-[10px] text-muted">
            {[
              ["bg-primary", "成功"],
              ["bg-orange-300", "失败"],
              ["bg-blue-300", "已执行 / 未验证"],
            ].map(([c, t]) => (
              <span className="flex items-center gap-1.5" key={t}>
                <i className={cn("h-1.5 w-1.5 rounded-full", c)} />
                {t}
              </span>
            ))}
          </div>
        </section>
        <section className="card p-6">
          <p className="eyebrow text-primary">QUICK ACTIONS</p>
          <h2 className="mb-5 mt-2 font-semibold">下一步，轻松一点</h2>
          <div className="space-y-3">
            <QuickAction
              icon={Play}
              title="执行全部签到"
              detail="立即运行所有已启用的签到任务"
              onClick={async () => {
                if (!tasks.some((t) => t.kind === "signer" && t.enabled)) {
                  toast.info("还没有启用的签到任务");
                  return;
                }
                await post("/tasks/run-all");
                refresh();
                toast.success("任务已加入执行队列");
              }}
            />
            <QuickAction
              icon={RefreshCw}
              title="同步 Telegram 会话"
              detail="检查账号连接与授权状态"
              onClick={async () => {
                if (!accounts.length) {
                  setAuthOpen(true);
                  return;
                }
                const results = await post("/accounts/sync");
                refresh();
                if (results.some((r: any) => r.status === "error"))
                  throw new Error("部分会话验证失败，请查看账号页面");
                toast.success("会话同步完成");
              }}
            />
            <QuickAction
              icon={TerminalSquare}
              title="打开实时终端"
              detail="查看任务与 Telegram 交互日志"
              onClick={() => setPage("logs")}
            />
          </div>
          <div className="mt-5 rounded-lg bg-[#f4f9f7] p-3 text-[11px] leading-6 text-muted">
            <span className="font-medium text-primary">小提示</span>
            <br />
            为签到设置随机延迟，让执行时间自然分散。
          </div>
        </section>
      </div>
      <div className="grid gap-6 xl:grid-cols-[1fr_310px]">
        <section className="card overflow-hidden">
          <div className="flex items-center justify-between px-6 py-5">
            <h2 className="text-sm font-semibold">最近执行记录</h2>
            <button
              onClick={() => setPage("logs")}
              className="flex items-center gap-1 text-xs text-muted hover:text-primary"
            >
              查看全部
              <ArrowRight size={13} />
            </button>
          </div>
          {data?.recent?.length ? (
            <div className="divide-y divide-line">
              {data.recent.map((r: Run) => (
                <div key={r.id} className="flex items-center gap-3 px-6 py-4">
                  <div className="rounded-lg bg-slate-50 p-2 text-muted">
                    <Bot size={17} />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">
                      {r.task_name}
                    </p>
                    <p className="mt-1 text-[11px] text-muted">
                      {r.account_name} · {date(r.started_at)}
                    </p>
                  </div>
                  <Status value={r.status} />
                </div>
              ))}
            </div>
          ) : (
            <Empty
              title="新的一天，从自动化开始"
              description="绑定账号并创建任务后，这里会展示真实的执行记录与结果。"
              action={
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-2"
                  onClick={newTask}
                >
                  <Plus size={14} />
                  创建第一个任务
                </Button>
              }
            />
          )}
        </section>
        <section className="card p-6">
          <div className="mb-5 flex items-center justify-between">
            <h2 className="text-sm font-semibold">工作空间状态</h2>
            <Activity size={16} className="text-primary" />
          </div>
          <div className="space-y-4 text-xs">
            <div className="flex justify-between">
              <span className="text-muted">任务调度器</span>
              <span className="flex items-center gap-1.5 text-primary">
                <i className="h-1.5 w-1.5 rounded-full bg-primary" />
                {data ? "运行中" : "连接中"}
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted">当前时区</span>
              <span>{data?.timezone || "—"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted">签到 / 自动化 / 监控</span>
              <span>
                {tasks.filter((t) => t.kind === "signer").length} /{" "}
                {tasks.filter((t) => t.kind === "automation").length} /{" "}
                {tasks.filter((t) => t.kind === "monitor").length}
              </span>
            </div>
          </div>
          <div className="my-5 border-t border-line" />
          <h3 className="mb-3 text-xs font-medium">开始使用</h3>
          {[
            [
              "连接 Telegram 账号",
              accounts.length > 0,
              () => setAuthOpen(true),
            ],
            ["创建自动化任务", tasks.length > 0, newTask],
            [
              "配置失败通知",
              data?.notifications_enabled,
              () => setPage("settings"),
            ],
          ].map(([label, done, action]: any, i) => (
            <button
              key={label}
              className="mb-3 flex w-full items-center gap-2 text-left text-xs text-muted"
              onClick={action}
            >
              <span
                className={cn(
                  "flex h-5 w-5 items-center justify-center rounded-full text-[10px]",
                  done
                    ? "bg-emerald-100 text-primary"
                    : "bg-slate-100 text-slate-400",
                )}
              >
                {done ? <Check size={12} /> : i + 1}
              </span>
              {label}
              <ChevronRight size={12} className="ml-auto text-slate-300" />
            </button>
          ))}
        </section>
      </div>
    </div>
  );
}
function QuickAction({
  icon: Icon,
  title,
  detail,
  onClick,
}: {
  icon: any;
  title: string;
  detail: string;
  onClick: () => any;
}) {
  const [busy, setBusy] = useState(false);
  return (
    <button
      disabled={busy}
      onClick={async () => {
        setBusy(true);
        try {
          await onClick();
        } catch (e) {
          toast.error((e as Error).message);
        } finally {
          setBusy(false);
        }
      }}
      className="group flex w-full items-center gap-3 rounded-lg border border-line px-3 py-3 text-left transition hover:border-emerald-200 hover:bg-emerald-50/30 disabled:opacity-50"
    >
      <div className="rounded-lg bg-slate-50 p-2 text-muted group-hover:text-primary">
        {busy ? (
          <Loader2 size={16} className="animate-spin" />
        ) : (
          <Icon size={16} />
        )}
      </div>
      <div>
        <p className="text-xs font-medium">{title}</p>
        <p className="mt-1 text-[10px] text-muted">{detail}</p>
      </div>
      <ChevronRight size={13} className="ml-auto text-slate-300" />
    </button>
  );
}
function EditAccount({
  account,
  onClose,
}: {
  account: Account;
  onClose: () => void;
}) {
  const refresh = useUI((s) => s.refresh);
  const [name, setName] = useState(account.name);
  const [proxy, setProxy] = useState("");
  const [updateProxy, setUpdateProxy] = useState(false);
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent title="编辑账号" description={account.phone}>
        <div className="space-y-5">
          <Field label="账号备注">
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={updateProxy}
              onChange={(e) => setUpdateProxy(e.target.checked)}
            />
            更改独立代理
          </label>
          {updateProxy && (
            <Field
              label="代理地址"
              hint="留空使用全局代理，保存后会重新连接该账号。"
            >
              <input
                value={proxy}
                onChange={(e) => setProxy(e.target.value)}
                placeholder="socks5://host:1080"
              />
            </Field>
          )}
          <ActionButton
            className="w-full"
            disabled={!name}
            action={async () => {
              await api(`/accounts/${account.id}`, {
                method: "PUT",
                body: JSON.stringify({
                  name,
                  ...(updateProxy ? { proxy } : {}),
                }),
              });
              refresh();
              onClose();
            }}
            success="账号已更新"
          >
            保存设置
          </ActionButton>
        </div>
      </DialogContent>
    </Dialog>
  );
}
