"use client";
import { useState } from "react";
import {
  ArrowDown,
  ArrowUp,
  Braces,
  CheckCheck,
  FileUp,
  Plus,
  Save,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import { Account, api, post, Task } from "@/lib/api";
import { useUI } from "@/lib/store";
import { cn } from "@/lib/utils";
import { parseSchedule, scheduleCron } from "@/lib/schedule";
import { SchedulePicker } from "./schedule-picker";
import { ActionButton, Field } from "./common";
import { Button } from "./ui/button";
import { Dialog, DialogContent } from "./ui/dialog";
import { RuleEditor } from "./rule-editor";
export const templates: any = {
  signer: {
    chats: [
      {
        chat_id: "@YourBot",
        actions: [{ action: 1, text: "/checkin" }],
        action_interval: 1,
      },
    ],
    sign_at: "0 8 * * *",
    random_seconds: 300,
    sign_interval: 1,
  },
  automation: {
    rules: [
      {
        id: "auto_reply",
        enabled: true,
        triggers: [{ type: "message", params: { chat_id: "@YourGroup" } }],
        filters: { text_rule: "contains", text_value: "签到" },
        handlers: [{ handler: "send_text", params: { text: "已收到" } }],
        vars: {},
      },
    ],
  },
  monitor: {
    match_cfgs: [
      {
        chat_id: "@YourGroup",
        rule: "contains",
        rule_value: "关键词",
        always_ignore_me: true,
        default_send_text: "收到",
        ignore_case: true,
      },
    ],
  },
};
const automationTemplates: any = {
  relay: {
    rules: [{
      id: "relay_channels",
      enabled: true,
      triggers: [{ type: "message", params: { chat_ids: ["@SourceChannel"] } }],
      filters: { text_rule: "all", ignore_case: true },
      handlers: [{
        handler: "relay_message",
        params: {
          ignore_self: true,
          targets: [{ chat_id: "@TargetChannel", mode: "forward", dedup: "message" }],
        },
      }],
      vars: {},
    }],
  },
  relay_115: {
    rules: [{
      id: "long_anime_links",
      enabled: true,
      triggers: [{ type: "message", params: { chat_ids: ["@SourceChannel"] } }],
      filters: {
        text_rule: "regex",
        text_value: String.raw`\A20\d{2}｜长时｜连载｜国漫｜[^\r\n]+(?:\r?\n[ \t]*)+https?://115cdn\.com/s/[^\s\r\n]+[ \t]*(?:\r?\n[ \t]*)*\Z`,
        ignore_case: true,
      },
      handlers: [{
        handler: "relay_message",
        params: {
          ignore_self: true,
          targets: [{
            chat_id: "@Your115SaveBot",
            mode: "extract",
            pattern: String.raw`(https?://115cdn\.com/s/[^\s]+)`,
            group: 1,
            dedup: "content",
          }],
        },
      }],
      vars: {},
    }],
  },
  reply: templates.automation,
  cooldown: {
    rules: [
      {
        id: "cooldown",
        enabled: true,
        triggers: [
          {
            type: "timer",
            id: "next",
            params: { chat_id: "@YourBot", interval_seconds: 3600 },
          },
          { type: "message", params: { chat_id: "@YourBot" } },
        ],
        handlers: [
          {
            handler: "extract_regex",
            params: { pattern: "(\\d+)分钟", var: "minutes" },
          },
          {
            handler: "schedule_next",
            params: {
              from_var: "minutes",
              from_var_unit: "minutes",
              offset_seconds: 30,
              trigger_id: "next",
            },
          },
          { handler: "send_text", params: { text: "/checkin" } },
        ],
      },
    ],
  },
  ai: {
    rules: [
      {
        id: "ai_reply",
        triggers: [
          {
            type: "timer",
            params: { interval_seconds: 3600, chat_id: "@YourGroup" },
          },
        ],
        handlers: [
          {
            handler: "ai_reply",
            params: {
              prompt: "基于最近消息用中文自然回复一句话",
              recent_limit: 8,
              store_var: "answer",
            },
          },
          {
            handler: "blacklist_filter",
            params: { source_var: "answer", keywords: ["广告", "返利"] },
          },
          { handler: "send_text", params: { text: "{answer}" } },
        ],
      },
    ],
  },
};
export function TaskEditor({
  task,
  kind,
  accounts,
  onClose,
}: {
  task?: Task;
  kind: Task["kind"];
  accounts: Account[];
  onClose: () => void;
}) {
  const refresh = useUI((s) => s.refresh);
  const [schedule, setSchedule] = useState(() =>
    parseSchedule(task?.cron || "0 8 * * *"),
  );
  const [form, setForm] = useState<any>(
    task || {
      name: "",
      account_id: accounts[0]?.id || 0,
      kind,
      enabled: true,
      cron: "0 8 * * *",
      timezone: "Asia/Shanghai",
      delay_min: 10,
      delay_max: 300,
      success_pattern: "",
      failure_pattern: "",
      response_timeout: 20,
      folder: "",
    },
  );
  const [config, setConfig] = useState<any>(
    task?.config || structuredClone(templates[kind]),
  );
  const [raw, setRaw] = useState(
    JSON.stringify(task?.config || templates[kind], null, 2),
  );
  const [advanced, setAdvanced] = useState(false);
  const [error, setError] = useState("");
  const change = (key: string, value: any) =>
    setForm((old: any) => ({ ...old, [key]: value }));
  const updateConfig = (next: any) => {
    setConfig(next);
    setRaw(JSON.stringify(next, null, 2));
  };
  const payload = () => {
    const cron = kind === "signer" ? scheduleCron(schedule) : form.cron;
    const cfg = advanced ? JSON.parse(raw) : config;
    return {
      ...form,
      cron,
      config:
        kind === "signer"
          ? { ...cfg, sign_at: cron, random_seconds: form.delay_max }
          : cfg,
    };
  };
  const updateChat = (index: number, key: string, value: any) => {
    const next = structuredClone(config);
    next.chats[index][key] = value;
    updateConfig(next);
  };
  const updateAction = (chatIndex: number, index: number, value: any) => {
    const next = structuredClone(config);
    next.chats[chatIndex].actions[index] = value;
    updateConfig(next);
  };
  const save = async () => {
    setError("");
    try {
      await api(task ? `/tasks/${task.id}` : "/tasks", {
        method: task ? "PUT" : "POST",
        body: JSON.stringify(payload()),
      });
      refresh();
      onClose();
    } catch (e) {
      setError((e as Error).message);
      throw e;
    }
  };
  const importFile = async (file?: File) => {
    if (!file) return;
    try {
      const data = new FormData();
      data.set("file", file);
      const imported = await api("/config/parse", {
        method: "POST",
        body: data,
      });
      updateConfig(imported);
      setAdvanced(true);
      if (imported.sign_at) {
        const time = String(imported.sign_at);
        const cron = time.includes(":")
          ? `${Number(time.split(":")[1])} ${Number(time.split(":")[0])} * * *`
          : time;
        change("cron", cron);
        setSchedule(parseSchedule(cron));
      }
      if (imported.random_seconds !== undefined) {
        change("delay_min", 0);
        change("delay_max", imported.random_seconds);
      }
      toast.success("配置已导入，保存前可校验");
    } catch (e) {
      toast.error((e as Error).message);
    }
  };
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent
        className="max-w-3xl"
        title={
          task
            ? "编辑任务"
            : kind === "signer"
              ? "新建签到任务"
              : kind === "automation"
                ? "新建自动化规则"
                : "新建消息监控"
        }
        description="设置执行账号与动作，任务将按您的配置自动运行。"
      >
        <div className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="任务名称">
              <input
                value={form.name}
                onChange={(e) => change("name", e.target.value)}
                maxLength={100}
                placeholder="例如：每日积分签到"
              />
            </Field>
            <Field label="执行账号">
              <select
                value={form.account_id}
                onChange={(e) => change("account_id", Number(e.target.value))}
              >
                {!accounts.length && (
                  <option value={0}>请先添加 Telegram 账号</option>
                )}
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name} · {a.phone}
                  </option>
                ))}
              </select>
            </Field>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-3">
            <h3 className="font-semibold">
              {kind === "signer" ? "执行动作" : "规则配置"}
            </h3>
            <div className="flex items-center gap-2">
              <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted">
                <FileUp size={14} />
                导入 JSON / YAML
                <input
                  type="file"
                  className="hidden"
                  accept=".json,.yaml,.yml"
                  onChange={(e) => void importFile(e.target.files?.[0])}
                />
              </label>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  if (advanced) {
                    try {
                      setConfig(JSON.parse(raw));
                      setAdvanced(false);
                    } catch {
                      toast.error("JSON 语法错误");
                    }
                  } else {
                    setRaw(JSON.stringify(config, null, 2));
                    setAdvanced(true);
                  }
                }}
              >
                <Braces size={14} />
                {advanced
                  ? kind === "signer"
                    ? "表单编辑"
                    : "可视化编辑"
                  : kind === "signer"
                    ? "完整 JSON"
                    : "高级 JSON"}
              </Button>
            </div>
          </div>
          {kind === "automation" && !advanced && (
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
              <span>从模板开始</span>
              {[
                ["relay", "通用消息转发"],
                ["relay_115", "115 链接转存 Bot 示例"],
                ["reply", "关键词回复"],
                ["cooldown", "冷却时间调度"],
                ["ai", "AI + 黑名单"],
              ].map(([id, label]) => (
                <Button
                  key={id}
                  variant="outline"
                  size="sm"
                  onClick={() =>
                    updateConfig(structuredClone(automationTemplates[id]))
                  }
                >
                  {label}
                </Button>
              ))}
            </div>
          )}
          {advanced ? (
            <>
              <textarea
                aria-label="完整任务配置 JSON"
                className="code-editor min-h-72 !bg-slate-50"
                value={raw}
                onChange={(e) => setRaw(e.target.value)}
                spellCheck={false}
              />
              {kind === "automation" && (
                <p className="text-xs leading-6 text-muted">
                  支持 message / timer / startup 触发器，以及
                  relay_message（通用多目标转发）、send_text、reply_text、extract_regex、random_pick、delay、schedule_next、ai_reply、blacklist_filter、forward、external_forward、server_chan、store_state、load_state
                  和自定义 handler。
                </p>
              )}
              {kind === "monitor" && (
                <p className="text-xs leading-6 text-muted">
                  兼容上游全部 MatchConfig 字段：用户过滤、exact / contains /
                  regex、捕获组模板、AI 回复、Telegram / HTTP / UDP
                  转发、Server酱推送及延时删除。
                </p>
              )}
            </>
          ) : kind === "signer" ? (
            <div className="space-y-4">
              {config.chats?.map((chat: any, ci: number) => (
                <div
                  key={ci}
                  className="rounded-xl border border-line bg-slate-50/60 p-4"
                >
                  <div className="mb-4 flex items-center justify-between">
                    <span className="eyebrow text-muted">
                      目标 {String(ci + 1).padStart(2, "0")}
                    </span>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label="删除目标"
                      disabled={config.chats.length < 2}
                      onClick={() =>
                        updateConfig({
                          ...config,
                          chats: config.chats.filter(
                            (_: any, i: number) => i !== ci,
                          ),
                        })
                      }
                    >
                      <Trash2 size={14} />
                    </Button>
                  </div>
                  <div className="mb-4 grid gap-3 sm:grid-cols-3">
                    <Field label="Bot / Chat ID" className="sm:col-span-2">
                      <input
                        value={chat.chat_id}
                        onChange={(e) =>
                          updateChat(ci, "chat_id", e.target.value)
                        }
                        placeholder="@UnihostBot 或 -100…"
                      />
                    </Field>
                    <Field label="话题 ID（可选）">
                      <input
                        type="number"
                        value={chat.message_thread_id ?? ""}
                        onChange={(e) =>
                          updateChat(
                            ci,
                            "message_thread_id",
                            e.target.value ? Number(e.target.value) : null,
                          )
                        }
                      />
                    </Field>
                  </div>
                  <div className="space-y-2">
                    {chat.actions?.map((action: any, i: number) => (
                      <div key={i} className="flex items-center gap-2">
                        <span className="w-4 shrink-0 text-xs text-muted">
                          {i + 1}
                        </span>
                        <select
                          aria-label={`动作 ${i + 1} 类型`}
                          className="!w-40 shrink-0"
                          value={action.action}
                          onChange={(e) => {
                            const a = Number(e.target.value);
                            updateAction(ci, i, {
                              action: a,
                              ...(a === 1 || a === 3
                                ? { text: "" }
                                : a === 2
                                  ? { dice: "🎲" }
                                  : {}),
                            });
                          }}
                        >
                          {[
                            [1, "发送文本 / 指令"],
                            [2, "发送骰子"],
                            [3, "点击内联按钮"],
                            [4, "AI 图片识别"],
                            [5, "AI 计算题"],
                          ].map(([value, label]) => (
                            <option key={value} value={value}>
                              {label}
                            </option>
                          ))}
                        </select>
                        {[1, 3].includes(action.action) ? (
                          <input
                            aria-label={`动作 ${i + 1} 文本`}
                            placeholder={
                              action.action === 1 ? "/checkin" : "按钮上的文字"
                            }
                            value={action.text || ""}
                            onChange={(e) =>
                              updateAction(ci, i, {
                                ...action,
                                text: e.target.value,
                              })
                            }
                          />
                        ) : action.action === 2 ? (
                          <select
                            aria-label="骰子类型"
                            value={action.dice}
                            onChange={(e) =>
                              updateAction(ci, i, {
                                ...action,
                                dice: e.target.value,
                              })
                            }
                          >
                            {["🎲", "🎯", "🏀", "⚽", "🎳", "🎰"].map((v) => (
                              <option key={v}>{v}</option>
                            ))}
                          </select>
                        ) : (
                          <span className="flex-1 text-xs text-muted">
                            使用系统设置中的 AI 配置
                          </span>
                        )}
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label="上移动作"
                          disabled={i === 0}
                          onClick={() => {
                            const a = [...chat.actions];
                            [a[i - 1], a[i]] = [a[i], a[i - 1]];
                            updateChat(ci, "actions", a);
                          }}
                        >
                          <ArrowUp size={13} />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label="下移动作"
                          disabled={i === chat.actions.length - 1}
                          onClick={() => {
                            const a = [...chat.actions];
                            [a[i + 1], a[i]] = [a[i], a[i + 1]];
                            updateChat(ci, "actions", a);
                          }}
                        >
                          <ArrowDown size={13} />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label="删除动作"
                          disabled={chat.actions.length < 2}
                          onClick={() =>
                            updateChat(
                              ci,
                              "actions",
                              chat.actions.filter(
                                (_: any, n: number) => n !== i,
                              ),
                            )
                          }
                        >
                          <Trash2 size={13} />
                        </Button>
                      </div>
                    ))}
                  </div>
                  <Button
                    className="my-3"
                    variant="outline"
                    size="sm"
                    onClick={() =>
                      updateChat(ci, "actions", [
                        ...chat.actions,
                        { action: 1, text: "" },
                      ])
                    }
                  >
                    <Plus size={13} />
                    添加动作
                  </Button>
                  <div className="grid grid-cols-2 gap-3">
                    <Field label="动作间隔（秒）">
                      <input
                        type="number"
                        min={0}
                        value={chat.action_interval ?? 1}
                        onChange={(e) =>
                          updateChat(
                            ci,
                            "action_interval",
                            Number(e.target.value),
                          )
                        }
                      />
                    </Field>
                    <Field
                      label="延时删除消息（秒）"
                      hint="留空保留消息，0 表示立即删除。"
                    >
                      <input
                        type="number"
                        min={0}
                        value={chat.delete_after ?? ""}
                        onChange={(e) =>
                          updateChat(
                            ci,
                            "delete_after",
                            e.target.value ? Number(e.target.value) : null,
                          )
                        }
                      />
                    </Field>
                  </div>
                </div>
              ))}
              <Button
                variant="outline"
                className="w-full border-dashed"
                onClick={() =>
                  updateConfig({
                    ...config,
                    chats: [
                      ...config.chats,
                      {
                        chat_id: "",
                        actions: [{ action: 1, text: "/checkin" }],
                        action_interval: 1,
                      },
                    ],
                  })
                }
              >
                <Plus size={15} />
                添加目标 Bot / 群组
              </Button>
            </div>
          ) : (
            <RuleEditor kind={kind} config={config} onChange={updateConfig} />
          )}
          {kind === "signer" && (
            <>
              <div className="border-t border-line pt-5">
                <h3 className="mb-4 font-semibold">调度与结果校验</h3>
                <SchedulePicker value={schedule} onChange={setSchedule} />
                <div className="mt-4 grid gap-4 sm:grid-cols-2">
                  <Field label="调度时区">
                    <input
                      value={form.timezone}
                      onChange={(e) => change("timezone", e.target.value)}
                    />
                  </Field>
                  <Field label="随机延迟下限（秒）">
                    <input
                      type="number"
                      min={0}
                      value={form.delay_min}
                      onChange={(e) =>
                        change("delay_min", Number(e.target.value))
                      }
                    />
                  </Field>
                  <Field label="随机延迟上限（秒）">
                    <input
                      type="number"
                      min={form.delay_min}
                      value={form.delay_max}
                      onChange={(e) =>
                        change("delay_max", Number(e.target.value))
                      }
                    />
                  </Field>
                  <Field
                    label="成功正则"
                    hint="留空仅记录动作执行完成，不计为已验证签到成功。"
                  >
                    <input
                      value={form.success_pattern}
                      onChange={(e) =>
                        change("success_pattern", e.target.value)
                      }
                      placeholder="签到成功|获得.*积分"
                    />
                  </Field>
                  <Field label="失败正则">
                    <input
                      value={form.failure_pattern}
                      onChange={(e) =>
                        change("failure_pattern", e.target.value)
                      }
                      placeholder="签到失败|错误"
                    />
                  </Field>
                  <Field label="回复等待超时（秒）">
                    <input
                      type="number"
                      min={1}
                      max={300}
                      value={form.response_timeout}
                      onChange={(e) =>
                        change("response_timeout", Number(e.target.value))
                      }
                    />
                  </Field>
                  <Field label="目标间隔（秒）">
                    <input
                      type="number"
                      min={0}
                      value={config.sign_interval ?? 1}
                      onChange={(e) =>
                        updateConfig({
                          ...config,
                          sign_interval: Number(e.target.value),
                        })
                      }
                      disabled={advanced}
                    />
                  </Field>
                </div>
              </div>
            </>
          )}
          <Field
            label="从 Folder 发现对话（可选）"
            hint="普通 Telegram 文件夹名称或 ID；支持手动添加的对话。"
          >
            <input
              value={form.folder}
              onChange={(e) => change("folder", e.target.value)}
              placeholder="例如：Sign"
            />
          </Field>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => change("enabled", e.target.checked)}
              className="accent-emerald-600"
            />
            保存后启用{kind !== "signer" ? "并启动监听" : ""}
          </label>
          {error && (
            <p className="whitespace-pre-wrap rounded-lg bg-red-50 p-3 text-sm text-red-600">
              {error}
            </p>
          )}
          <div className="flex items-center justify-between gap-2 border-t border-line pt-5">
            <ActionButton
              variant="outline"
              action={async () => {
                await post("/tasks/validate", payload());
              }}
              success="配置校验通过"
            >
              <CheckCheck size={16} />
              校验配置
            </ActionButton>
            <div className="flex gap-2">
              <Button variant="ghost" onClick={onClose}>
                取消
              </Button>
              <ActionButton
                action={save}
                success="任务已保存"
                disabled={!form.name || !form.account_id}
              >
                <Save size={16} />
                保存任务
              </ActionButton>
            </div>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
