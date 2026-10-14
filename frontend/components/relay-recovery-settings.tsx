"use client";

import { useEffect, useState } from "react";
import { RotateCcw } from "lucide-react";
import { api } from "@/lib/api";
import { ActionButton, Field } from "./common";

type Policy = {
  enabled: boolean;
  max_attempts: number;
  base_delay: number;
  max_delay: number;
  horizon_hours: number;
};

export function RelayRecoverySettings() {
  const [form, setForm] = useState<Policy | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    api<{ policy: Policy }>("/relay/retry-policy", {
      signal: controller.signal,
    })
      .then((data) => {
        setForm(data.policy);
        setError("");
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, [revision]);
  if (!form)
    return (
      <section className="card p-6" role="status">
        {error || "正在加载重试策略…"}
        {error && (
          <button
            className="ml-2 underline"
            onClick={() => setRevision((n) => n + 1)}
          >
            重新加载
          </button>
        )}
      </section>
    );
  const change = <K extends keyof Policy>(key: K, value: Policy[K]) =>
    setForm({ ...form, [key]: value });
  return (
    <section className="card space-y-4 p-6">
      <h2 className="flex items-center gap-2 font-semibold">
        <RotateCcw size={19} className="text-primary" />
        转发可靠性
      </h2>
      <p className="text-sm leading-6 text-muted">
        自动重试仅适用于文本、正则提取和模板的明确临时拒绝。原生转发和媒体复制需人工处理。发送超时、断网和结果不确定的记录需先在「转发记录」核查。默认关闭。
      </p>
      <label className="flex items-center gap-2 text-sm font-medium">
        <input
          className="!w-4"
          type="checkbox"
          checked={form.enabled}
          onChange={(e) => change("enabled", e.target.checked)}
        />
        启用自动重试
      </label>
      <div className="grid gap-4 sm:grid-cols-2">
        {(
          [
            ["max_attempts", "自动处理次数上限", 1, 10],
            ["base_delay", "重试基础间隔（秒）", 30, 3600],
            ["max_delay", "最大退避间隔（秒）", 60, 86400],
            ["horizon_hours", "失败有效期（小时）", 1, 168],
          ] as const
        ).map(([key, label, min, max]) => (
          <Field key={key} label={label}>
            <input
              type="number"
              min={min}
              max={max}
              value={form[key]}
              onChange={(e) => change(key, Number(e.target.value))}
            />
          </Field>
        ))}
      </div>
      <ActionButton
        action={async () => {
          const data = await api<{ policy: Policy }>("/relay/retry-policy", {
            method: "PUT",
            body: JSON.stringify(form),
          });
          setForm(data.policy);
        }}
        success="重试策略已保存"
      >
        保存重试策略
      </ActionButton>
      <p className="text-xs leading-6 text-muted">
        保存后生效，每 30 秒检查，每轮最多处理 5
        条。间隔逐次延长，并遵守目标冷却及 Telegram
        要求的最短等待。次数包含来源核验；可在记录中单独暂停。关闭后停止领取新记录，已开始的处理可能完成。
      </p>
    </section>
  );
}
