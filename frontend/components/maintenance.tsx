"use client";

import { useEffect, useState } from "react";
import { Archive, BellRing } from "lucide-react";
import { toast } from "sonner";
import { api, post } from "@/lib/api";
import { ActionButton, Field } from "./common";

type Policy = {
  enabled: boolean;
  run_days: number;
  relay_sent_days: number;
  relay_failed_days: number;
  alert_enabled: boolean;
  alert_window_minutes: number;
  alert_cooldown_minutes: number;
};
type Cleanup = {
  time?: string;
  runs: number;
  relay: number;
  batch_size: number;
};
type State = {
  policy: Policy;
  batch_size: number;
  last_cleanup: Cleanup | null;
  alerts: { last_status: string; next_allowed?: number };
};

export function Maintenance() {
  const [state, setState] = useState<State | null>(null);
  const [form, setForm] = useState<Policy | null>(null);
  const [preview, setPreview] = useState<Cleanup | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    api<State>("/maintenance", { signal: controller.signal })
      .then((data) => {
        setState(data);
        setForm(data.policy);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, []);
  if (!state || !form)
    return (
      <section className="card p-6" role="status">
        {error || "正在加载维护策略…"}
      </section>
    );
  const dirty = JSON.stringify(state.policy) !== JSON.stringify(form);
  const change = <K extends keyof Policy>(key: K, value: Policy[K]) => {
    setForm({ ...form, [key]: value });
    setPreview(null);
  };
  return (
    <section className="card space-y-5 p-6">
      <h2 className="flex items-center gap-2 font-semibold">
        <Archive size={19} className="text-primary" />
        运行与历史保留
      </h2>
      <p className="text-sm leading-6 text-muted">
        按保存的策略每小时清理一批过期历史，默认关闭。运行中的记录始终保留。已成功发送的去重标记长期保留。
      </p>
      <label className="flex items-center gap-2 text-sm font-medium">
        <input
          className="!w-4"
          type="checkbox"
          checked={form.enabled}
          onChange={(e) => change("enabled", e.target.checked)}
        />
        启用历史清理
      </label>
      <div className="grid gap-4 sm:grid-cols-3">
        {(
          [
            ["run_days", "执行历史保留天数", 7],
            ["relay_sent_days", "成功转发保留天数", 7],
            ["relay_failed_days", "失败转发保留天数", 30],
          ] as const
        ).map(([key, label, min]) => (
          <Field key={key} label={label}>
            <input
              type="number"
              min={min}
              max={3650}
              value={form[key]}
              onChange={(e) => change(key, Number(e.target.value))}
            />
          </Field>
        ))}
      </div>
      <div className="border-t border-line pt-4">
        <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold">
          <BellRing size={16} />
          转发异常告警
        </h3>
        <label className="flex items-center gap-2 text-sm">
          <input
            className="!w-4"
            type="checkbox"
            checked={form.alert_enabled}
            onChange={(e) => change("alert_enabled", e.target.checked)}
          />
          启用转发异常汇总
        </label>
        <p className="mt-2 text-xs leading-6 text-muted">
          每分钟检查新增及积压失败，按账号、任务和错误类型汇总，通过下方已启用的通知渠道发送。冷却期间的失败会在冷却结束后合并通知，重启后仍有效。
        </p>
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <Field label="初次告警回看窗口（分钟）">
            <input
              type="number"
              min={1}
              max={60}
              value={form.alert_window_minutes}
              onChange={(e) =>
                change("alert_window_minutes", Number(e.target.value))
              }
            />
          </Field>
          <Field label="告警冷却时间（分钟）">
            <input
              type="number"
              min={5}
              max={1440}
              value={form.alert_cooldown_minutes}
              onChange={(e) =>
                change("alert_cooldown_minutes", Number(e.target.value))
              }
            />
          </Field>
        </div>
        <p className="mt-3 text-xs text-muted">
          最近告警：
          {{
            idle: "暂无",
            sending: "发送中",
            sent: "已发送",
            failed: "发送失败，请检查通知渠道",
          }[state.alerts.last_status] || "暂无"}
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        <ActionButton
          action={async () => {
            const result = await api<State>("/maintenance/policy", {
              method: "PUT",
              body: JSON.stringify(form),
            });
            setState(result);
            setForm(result.policy);
            setPreview(null);
          }}
          success="维护策略已保存"
        >
          保存维护策略
        </ActionButton>
        <ActionButton
          variant="outline"
          disabled={dirty}
          action={async () =>
            setPreview(await api<Cleanup>("/maintenance/preview"))
          }
        >
          预览可清理记录
        </ActionButton>
        <ActionButton
          variant="outline"
          disabled={!state.policy.enabled || dirty}
          action={async () => {
            if (
              !window.confirm(
                "按已保存策略清理一批过期历史？清理后的详细记录无法恢复，已成功发送的去重标记仍保留。",
              )
            )
              return;
            const result = await post<Cleanup>("/maintenance/cleanup");
            setState({ ...state, last_cleanup: result });
            setPreview(null);
            toast.success(
              `已清理执行历史 ${result.runs} 条、转发历史 ${result.relay} 条`,
            );
          }}
        >
          清理一批历史
        </ActionButton>
      </div>
      {dirty && (
        <p className="text-xs text-amber-700" role="status">
          请先保存维护策略，再预览或清理。
        </p>
      )}
      {preview && (
        <p className="text-sm" role="status">
          可清理：执行历史 {preview.runs} 条，转发历史 {preview.relay} 条
        </p>
      )}
      {state.last_cleanup && (
        <p className="text-xs text-muted">
          最近清理：
          {state.last_cleanup.time
            ? new Date(state.last_cleanup.time).toLocaleString("zh-CN")
            : "刚刚"}{" "}
          · 执行历史 {state.last_cleanup.runs} 条，转发历史{" "}
          {state.last_cleanup.relay} 条
        </p>
      )}
      <p className="text-xs leading-6 text-muted">
        每批各最多 {state.batch_size}{" "}
        条，大量过期记录会分批处理。历史统计以保留的记录为准；清理后空间供后续记录复用，文件大小可能暂不缩小。
      </p>
    </section>
  );
}
