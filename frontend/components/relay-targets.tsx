"use client";

import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { post } from "@/lib/api";
import { Field } from "./common";
import { Button } from "./ui/button";

type RelayTarget = {
  chat_id: string | number;
  mode: "forward" | "copy" | "text" | "extract" | "template";
  dedup?: "message" | "content" | "off";
  pattern?: string;
  group?: string | number;
  all_matches?: boolean;
  template?: string;
  retry_after_seconds?: number;
};

const MODE_LABELS: Record<RelayTarget["mode"], string> = {
  forward: "原样转发（保留来源）",
  copy: "复制消息（不显示来源）",
  text: "发送纯文本",
  extract: "正则提取后发送",
  template: "自定义文本模板",
};
const DEDUP_LABELS = {
  message: "按来源消息去重",
  content: "按发送内容去重",
  off: "关闭去重",
};

export const defaultRelayParams = () => ({
  ignore_self: true,
  targets: [
    {
      chat_id: "@TargetChannel",
      mode: "forward" as const,
      dedup: "message" as const,
    },
  ],
});

export function RegexTestPanel({
  mode,
  pattern,
  ignoreCase = true,
}: {
  mode: "all" | "contains" | "exact" | "regex";
  pattern: string;
  ignoreCase?: boolean;
}) {
  const [sample, setSample] = useState("");
  const [outcome, setOutcome] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const test = async () => {
    setBusy(true);
    try {
      const response = await post("/relay/preview", {
        text_rule: mode,
        pattern,
        sample,
        ignore_case: ignoreCase,
      });
      setOutcome(response);
    } catch (error) {
      toast.error((error as Error).message);
      setOutcome(null);
    } finally {
      setBusy(false);
    }
  };
  return (
    <details className="mt-3 rounded-lg border border-line bg-white/60 px-4 py-3 dark:bg-slate-800/50">
      <summary className="cursor-pointer text-sm font-semibold">
        测试消息匹配（正则支持捕获组预览）
      </summary>
      <div className="mt-3 space-y-3">
        <Field label="粘贴收到的 Telegram 消息内容">
          <textarea
            rows={4}
            value={sample}
            maxLength={8000}
            placeholder="在此粘贴实际消息，测试是否命中"
            onChange={(event) => setSample(event.target.value)}
          />
        </Field>
        <Button type="button" size="sm" variant="outline" disabled={busy} onClick={test}>
          {busy ? "测试中…" : "执行测试"}
        </Button>
        {outcome && (
          <div className="rounded-lg bg-slate-50 p-3 text-xs dark:bg-slate-900">
            <p className={outcome.matched ? "font-semibold text-emerald-700" : "font-semibold text-red-600"}>
              {outcome.matched ? "匹配成功" : "未匹配"}
            </p>
            {outcome.matched && <pre className="mt-2 max-h-44 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(outcome, null, 2)}</pre>}
          </div>
        )}
      </div>
    </details>
  );
}

export function RelayTargetsEditor({
  params,
  onChange,
}: {
  params: any;
  onChange: (params: any) => void;
}) {
  const targets: RelayTarget[] = params.targets || [];
  const editTarget = (index: number, patch: Partial<RelayTarget>) =>
    onChange({
      ...params,
      targets: targets.map((item, i) => (i === index ? { ...item, ...patch } : item)),
    });
  const add = () => {
    if (targets.length >= 25) {
      toast.error("最多设置 25 个转发目标");
      return;
    }
    onChange({
      ...params,
      targets: [
        ...targets,
        { chat_id: "", mode: "forward", dedup: "message" },
      ],
    });
  };
  const remove = (index: number) =>
    onChange({ ...params, targets: targets.filter((_item, i) => i !== index) });

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-xs leading-5 text-emerald-900 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-100">
        通用消息转发支持频道、群组、个人或 Bot。可以添加多个目标，每个目标独立选择转发模式和去重方式。
        发送成功仅代表 Telegram 已接受消息，不代表目标 Bot 完成后续任务。
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={params.ignore_self !== false}
          onChange={(event) => onChange({ ...params, ignore_self: event.target.checked })}
        />
        忽略自己发送的消息（避免自动转发循环）
      </label>
      {targets.map((target, index) => (
        <section key={index} className="rounded-lg border border-line bg-white/80 p-4 dark:bg-slate-800/60">
          <div className="mb-3 flex items-center justify-between">
            <span className="text-sm font-semibold">目标 {index + 1}</span>
            <Button type="button" variant="ghost" size="icon" aria-label="删除转发目标" onClick={() => remove(index)}>
              <Trash2 size={15} />
            </Button>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="发送到 Telegram Chat ID / @username">
              <input
                value={target.chat_id ?? ""}
                placeholder="@archive 或 -100123456789，收藏消息可填 me"
                onChange={(event) => editTarget(index, { chat_id: event.target.value })}
              />
            </Field>
            <Field label="转发模式">
              <select
                value={target.mode || "forward"}
                onChange={(event) => editTarget(index, { mode: event.target.value as RelayTarget["mode"] })}
              >
                {Object.entries(MODE_LABELS).map(([value, label]) =>
                  <option key={value} value={value}>{label}</option>
                )}
              </select>
            </Field>
            <Field label="去重策略">
              <select
                value={target.dedup || "message"}
                onChange={(event) => editTarget(index, { dedup: event.target.value as RelayTarget["dedup"] })}
              >
                {Object.entries(DEDUP_LABELS).map(([value, label]) =>
                  <option key={value} value={value}>{label}</option>
                )}
              </select>
            </Field>
            <Field label="失败冷却时间（秒）">
              <input
                type="number"
                min={0}
                value={target.retry_after_seconds ?? 600}
                onChange={(event) => editTarget(index, { retry_after_seconds: Math.max(0, Number(event.target.value)) })}
              />
            </Field>
          </div>
          {(target.mode === "extract" || target.mode === "template") && (
            <div className="mt-3 space-y-3">
              <Field label={target.mode === "extract" ? "提取正则（必填）" : "提取正则（可选）"}>
                <input
                  value={target.pattern || ""}
                  placeholder="例如：(https?://[^\s]+)"
                  onChange={(event) => editTarget(index, { pattern: event.target.value })}
                />
              </Field>
              {target.mode === "extract" && (
                <div className="grid gap-3 sm:grid-cols-2">
                  <Field label="捕获组（数字或命名组）">
                    <input
                      value={target.group ?? 1}
                      onChange={(event) => {
                        const value = event.target.value;
                        editTarget(index, { group: /^\d+$/.test(value) ? Number(value) : value });
                      }}
                    />
                  </Field>
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={Boolean(target.all_matches)}
                      onChange={(event) => editTarget(index, { all_matches: event.target.checked })}
                    />
                    提取所有匹配（最多 20 条）
                  </label>
                </div>
              )}
              {target.pattern && (
                <RegexTestPanel mode="regex" pattern={target.pattern} />
              )}
            </div>
          )}
          {target.mode === "template" && (
            <div className="mt-3">
              <Field label="消息模板（必填）">
                <textarea
                  rows={3}
                  value={target.template || ""}
                  placeholder="来自 {chat_id}：{text}"
                  onChange={(event) => editTarget(index, { template: event.target.value })}
                />
              </Field>
            </div>
          )}
        </section>
      ))}
      <Button type="button" size="sm" variant="outline" onClick={add}>
        <Plus size={15} /> 添加转发目标
      </Button>
      <p className="text-xs text-muted">
        对纯图片、视频和文件使用「原样转发」或「复制消息」。纯文本模式不带媒体。
      </p>
    </div>
  );
}
