"use client";
import { useEffect, useState } from "react";
import {
  BellRing,
  Globe2,
  KeyRound,
  Save,
  Shield,
  Sparkles,
} from "lucide-react";
import { toast } from "sonner";
import { api, post } from "@/lib/api";
import { ActionButton, Field } from "./common";
import { Button } from "./ui/button";
import { Maintenance } from "./maintenance";
import { BackupManagement } from "./backup-management";
import { PerformancePanel } from "./performance-panel";
import { RelayRecoverySettings } from "./relay-recovery-settings";
export function Settings() {
  const [form, setForm] = useState<any>(null);
  const [notifications, setNotifications] = useState("[]");
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    api("/settings")
      .then((data) => {
        setForm(data);
        setNotifications(JSON.stringify(data.notifications, null, 2));
      })
      .catch((e) => setError(e.message));
  }, []);
  const change = (k: string, v: any) => setForm((f: any) => ({ ...f, [k]: v }));
  if (!form)
    return <div className="card p-8">{error || "正在加载系统设置…"}</div>;
  const addNotification = (type: string) => {
    try {
      const all = JSON.parse(notifications);
      const values: any = {
        bark: { url: "https://api.day.app", key: "" },
        telegram: { token: "", chat_id: "" },
        serverchan: { key: "" },
        pushdeer: { url: "https://api2.pushdeer.com", key: "" },
        webhook: { url: "", headers: {} },
      };
      all.push({ type, enabled: true, ...values[type] });
      setNotifications(JSON.stringify(all, null, 2));
    } catch {
      toast.error("请先修正通知配置中的 JSON 语法");
    }
  };
  return (
    <div className="grid gap-6 xl:grid-cols-[1fr_300px]">
      <div className="space-y-6">
        <Maintenance />
        <BackupManagement />
        <PerformancePanel />
        <RelayRecoverySettings />
        <section className="card p-6">
          <h2 className="mb-5 flex items-center gap-2 font-semibold">
            <Globe2 size={19} className="text-primary" />
            网络与时间
          </h2>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field
              label="全局代理"
              hint={
                form.proxy_display
                  ? `当前：${form.proxy_display}；保留掩码表示不更改。`
                  : "留空直接连接；独立账号代理优先。"
              }
            >
              <input
                autoComplete="off"
                value={form.global_proxy}
                onChange={(e) => change("global_proxy", e.target.value)}
                placeholder="socks5://127.0.0.1:1080"
              />
            </Field>
            <Field label="统计时区" hint="任务可单独配置调度时区。">
              <input
                value={form.timezone}
                onChange={(e) => change("timezone", e.target.value)}
              />
            </Field>
          </div>
        </section>
        <section className="card p-6">
          <h2 className="mb-5 flex items-center gap-2 font-semibold">
            <Sparkles size={19} className="text-primary" />
            AI 能力
          </h2>
          <p className="mb-5 text-sm leading-6 text-muted">
            用于图片选项识别、计算题和自动化 AI 回复。支持 OpenAI 兼容接口。
          </p>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="API Key">
              <input
                type="password"
                autoComplete="off"
                value={form.ai_key}
                onChange={(e) => change("ai_key", e.target.value)}
              />
            </Field>
            <Field label="模型名称">
              <input
                value={form.ai_model}
                onChange={(e) => change("ai_model", e.target.value)}
              />
            </Field>
            <Field label="Base URL（可选）" className="sm:col-span-2">
              <input
                value={form.ai_base_url}
                onChange={(e) => change("ai_base_url", e.target.value)}
                placeholder="https://api.openai.com/v1"
              />
            </Field>
          </div>
        </section>
        <section className="card p-6">
          <h2 className="mb-3 flex items-center gap-2 font-semibold">
            <BellRing size={19} className="text-primary" />
            消息通知
          </h2>
          <p className="mb-4 text-sm leading-6 text-muted">
            签到失败或 Session
            失效时发送通知，可同时配置多个渠道。修改后先保存，再测试。
          </p>
          <div className="mb-4 flex flex-wrap gap-2">
            {[
              ["bark", "Bark"],
              ["telegram", "Telegram Bot"],
              ["serverchan", "Server酱"],
              ["pushdeer", "PushDeer"],
              ["webhook", "Webhook"],
            ].map(([type, label]) => (
              <Button
                key={type}
                variant="outline"
                size="sm"
                onClick={() => addNotification(type)}
              >
                + {label}
              </Button>
            ))}
          </div>
          <textarea
            aria-label="通知渠道配置"
            className="code-editor min-h-52"
            spellCheck={false}
            value={notifications}
            onChange={(e) => setNotifications(e.target.value)}
          />
          <ActionButton
            variant="outline"
            className="mt-4"
            action={async () => {
              const result = await post("/settings/test-notification");
              if (!result.length) throw new Error("请先保存至少一个通知渠道");
              if (result.some((r: any) => !r.ok))
                throw new Error(
                  result
                    .filter((r: any) => !r.ok)
                    .map((r: any) => `${r.type}: ${r.error}`)
                    .join("；"),
                );
              toast.success("测试通知已发送");
            }}
          >
            <BellRing size={15} />
            发送测试通知
          </ActionButton>
        </section>
        <ActionButton
          action={async () => {
            const result = await api("/settings", {
              method: "PUT",
              body: JSON.stringify({
                ...form,
                notifications: JSON.parse(notifications),
              }),
            });
            setForm(result);
            setNotifications(JSON.stringify(result.notifications, null, 2));
          }}
          success="系统设置已保存"
        >
          <Save size={16} />
          保存系统设置
        </ActionButton>
      </div>
      <aside className="space-y-6">
        <section className="card p-5">
          <h2 className="mb-5 flex items-center gap-2 font-semibold">
            <Shield size={18} className="text-primary" />
            管理面板安全
          </h2>
          <div className="space-y-4">
            <Field label="当前密码">
              <input
                type="password"
                autoComplete="current-password"
                value={current}
                onChange={(e) => setCurrent(e.target.value)}
              />
            </Field>
            <Field
              label="新密码"
              hint="至少 12 个字符，修改后其他登录会话将失效。"
            >
              <input
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </Field>
            <ActionButton
              variant="outline"
              className="w-full"
              disabled={password.length < 12 || !current}
              action={async () => {
                await api("/panel/password", {
                  method: "PUT",
                  body: JSON.stringify({
                    current_password: current,
                    new_password: password,
                  }),
                });
                setCurrent("");
                setPassword("");
              }}
              success="管理密码已更新"
            >
              <KeyRound size={15} />
              更新密码
            </ActionButton>
          </div>
        </section>
        <section className="rounded-xl border border-emerald-100 bg-emerald-50/60 p-5">
          <p className="eyebrow text-primary">运行环境</p>
          <p className="mt-4 text-sm">
            tg-signer{" "}
            <span className="font-mono font-semibold">
              {form.tg_signer_version}
            </span>
          </p>
          <p className="mt-2 text-xs leading-6 text-muted">
            单容器部署 · SQLite 持久化
            <br />
            Session / 密钥加密存储
            <br />
            HttpOnly Cookie · JWT 鉴权
          </p>
          <a
            href="/api/docs"
            target="_blank"
            rel="noreferrer"
            className="mt-4 inline-block text-sm font-medium text-primary"
          >
            查看 API 文档 ↗
          </a>
        </section>
      </aside>
    </div>
  );
}
