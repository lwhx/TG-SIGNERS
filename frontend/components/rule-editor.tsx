"use client";

import { Plus, Trash2 } from "lucide-react";
import { Button } from "./ui/button";
import { Field } from "./common";
import { RelayTargetsEditor, RegexTestPanel, defaultRelayParams } from "./relay-targets";

type RuleEditorProps = {
  kind: "automation" | "monitor";
  config: any;
  onChange: (config: any) => void;
};

const clone = <T,>(value: T): T => structuredClone(value);

const toList = (value: string) => {
  const items = value
    .split(/[,，\n]/)
    .map((item) => item.trim())
    .filter(Boolean);
  return items.length ? items : null;
};

const fromList = (value: unknown) =>
  Array.isArray(value) ? value.join(", ") : value == null ? "" : String(value);

function EditorIntro({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-emerald-200/70 bg-emerald-50/70 px-4 py-3 text-sm leading-6 text-emerald-900 dark:border-emerald-900/70 dark:bg-emerald-950/40 dark:text-emerald-100">
      {children}
    </div>
  );
}

function CheckField({
  checked,
  label,
  onChange,
}: {
  checked: boolean;
  label: string;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-2 text-sm font-normal text-ink">
      <input
        type="checkbox"
        className="accent-emerald-600"
        checked={Boolean(checked)}
        onChange={(event) => onChange(event.target.checked)}
      />
      {label}
    </label>
  );
}

function MonitorEditor({ config, onChange }: Omit<RuleEditorProps, "kind">) {
  const matchCfgs = config.match_cfgs || [];
  const update = (index: number, key: string, value: any) => {
    const next = clone(config);
    next.match_cfgs[index] = { ...next.match_cfgs[index], [key]: value };
    onChange(next);
  };
  const add = () =>
    onChange({
      ...clone(config),
      match_cfgs: [
        ...matchCfgs,
        {
          chat_id: "",
          rule: "contains",
          rule_value: "关键词",
          always_ignore_me: true,
          default_send_text: "",
          ignore_case: true,
        },
      ],
    });
  const remove = (index: number) =>
    onChange({
      ...clone(config),
      match_cfgs: matchCfgs.filter((_item: any, itemIndex: number) => itemIndex !== index),
    });

  return (
    <div className="space-y-4">
      <EditorIntro>
        用表单描述“在哪个对话里，匹配什么消息，以及匹配后如何回复”。常用配置不需要编写 JSON；需要更复杂的转发时仍可切换到高级模式。
      </EditorIntro>
      {matchCfgs.map((item: any, index: number) => (
        <section key={index} className="rounded-xl border border-line bg-slate-50/60 p-4 dark:bg-slate-900/40">
          <div className="mb-4 flex items-center justify-between gap-3">
            <div>
              <p className="eyebrow text-muted">监控条件 {String(index + 1).padStart(2, "0")}</p>
              <p className="mt-1 text-xs text-muted">消息满足以下条件时执行回复或通知</p>
            </div>
            <Button
              variant="ghost"
              size="icon"
              aria-label="删除监控条件"
              disabled={matchCfgs.length < 2}
              onClick={() => remove(index)}
            >
              <Trash2 size={14} />
            </Button>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="来源对话" hint="支持 @用户名或 Telegram 数字 ID。">
              <input
                value={item.chat_id ?? ""}
                onChange={(event) => update(index, "chat_id", event.target.value)}
                placeholder="例如：@YourGroup 或 -100123456789"
              />
            </Field>
            <Field label="匹配方式">
              <select
                value={item.rule || "contains"}
                onChange={(event) => {
                  const rule = event.target.value;
                  const next = clone(config);
                  next.match_cfgs[index] = {
                    ...next.match_cfgs[index],
                    rule,
                    ...(rule === "all" ? { rule_value: null } : {}),
                  };
                  onChange(next);
                }}
              >
                <option value="contains">包含关键词</option>
                <option value="exact">完全相同</option>
                <option value="regex">正则表达式</option>
                <option value="all">所有消息</option>
              </select>
            </Field>
            {item.rule !== "all" && (
              <Field label={item.rule === "regex" ? "匹配表达式" : "关键词"} className="sm:col-span-2">
                <input
                  value={item.rule_value ?? ""}
                  onChange={(event) => update(index, "rule_value", event.target.value)}
                  placeholder={item.rule === "regex" ? "例如：积分[：:]\\s*(\\d+)" : "例如：签到成功"}
                />
              </Field>
            )}
            <Field label="限定发送者" hint="多个发送者用逗号分隔，留空表示所有人。">
              <input
                value={fromList(item.from_user_ids)}
                onChange={(event) => update(index, "from_user_ids", toList(event.target.value))}
                placeholder="例如：@admin, 123456"
              />
            </Field>
            <Field label="默认回复内容" hint="留空则只记录匹配，不自动回复。">
              <input
                value={item.default_send_text ?? ""}
                onChange={(event) => update(index, "default_send_text", event.target.value || null)}
                placeholder="例如：收到"
              />
            </Field>
          </div>
          <div className="mt-4 grid gap-3 border-t border-line pt-4 sm:grid-cols-2">
            <CheckField checked={item.always_ignore_me} label="忽略自己发送的消息" onChange={(value) => update(index, "always_ignore_me", value)} />
            <CheckField checked={item.ignore_case !== false} label="忽略大小写" onChange={(value) => update(index, "ignore_case", value)} />
            <CheckField checked={item.ai_reply} label="使用 AI 生成回复" onChange={(value) => update(index, "ai_reply", value)} />
            <CheckField checked={item.push_via_server_chan} label="通过 Server酱推送" onChange={(value) => update(index, "push_via_server_chan", value)} />
          </div>
          {(item.ai_reply || item.push_via_server_chan) && (
            <div className="mt-4 grid gap-4 border-t border-line pt-4 sm:grid-cols-2">
              {item.ai_reply && (
                <Field label="AI 回复提示词" className="sm:col-span-2">
                  <textarea
                    rows={2}
                    value={item.ai_prompt ?? ""}
                    onChange={(event) => update(index, "ai_prompt", event.target.value || null)}
                    placeholder="例如：请用中文简短回复这条消息"
                  />
                </Field>
              )}
              {item.push_via_server_chan && (
                <Field label="Server酱 SendKey" className="sm:col-span-2" hint="也可以留空，使用系统通知设置中的 SendKey。">
                  <input
                    type="password"
                    value={item.server_chan_send_key ?? ""}
                    onChange={(event) => update(index, "server_chan_send_key", event.target.value || null)}
                    placeholder="SendKey"
                  />
                </Field>
              )}
            </div>
          )}
          <details className="mt-4 border-t border-line pt-4">
            <summary className="cursor-pointer text-xs font-medium text-muted hover:text-ink">更多回复选项</summary>
            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <Field label="提取内容的正则" hint="匹配后把第一个捕获组作为回复内容。">
                <input value={item.send_text_search_regex ?? ""} onChange={(event) => update(index, "send_text_search_regex", event.target.value || null)} placeholder="例如：验证码[:：]\\s*(\\d+)" />
              </Field>
              <Field label="回复模板" hint="可使用 {extracted}、{message_text}。">
                <input value={item.send_text_template ?? ""} onChange={(event) => update(index, "send_text_template", event.target.value || null)} placeholder="例如：验证码是 {extracted}" />
              </Field>
              <Field label="转发到对话">
                <input value={item.forward_to_chat_id ?? ""} onChange={(event) => update(index, "forward_to_chat_id", event.target.value || null)} placeholder="可选的 @用户名或数字 ID" />
              </Field>
              <Field label="匹配后延时删除（秒）">
                <input type="number" min={0} value={item.delete_after ?? ""} onChange={(event) => update(index, "delete_after", event.target.value ? Number(event.target.value) : null)} placeholder="不删除" />
              </Field>
            </div>
          </details>
        </section>
      ))}
      <Button variant="outline" className="w-full border-dashed" onClick={add}>
        <Plus size={15} />
        添加监控条件
      </Button>
    </div>
  );
}

const handlerOptions = [
  ["send_text", "发送文本"],
  ["reply_text", "回复消息"],
  ["ai_reply", "AI 回复"],
  ["blacklist_filter", "黑名单过滤"],
  ["delay", "延时"],
  ["extract_regex", "提取正则变量"],
  ["forward", "转发消息"],
  ["relay_message", "通用多目标消息转发"],
  ["random_pick", "随机回复"],
  ["server_chan", "Server酱通知"],
  ["schedule_next", "安排下次执行"],
  ["store_state", "保存变量"],
  ["load_state", "读取变量"],
] as const;

function paramsFor(handler: string) {
  switch (handler) {
    case "send_text":
    case "reply_text":
      return { text: "已收到" };
    case "ai_reply":
      return { prompt: "请用中文自然回复这条消息", recent_limit: 8 };
    case "blacklist_filter":
      return { keywords: ["广告", "返利"] };
    case "delay":
      return { seconds: 3 };
    case "extract_regex":
      return { pattern: "(\\d+)", var: "value" };
    case "forward":
      return { chat_id: "@TargetChat" };
    case "relay_message":
      return defaultRelayParams();
    case "random_pick":
      return { choices: ["选项一", "选项二"] };
    case "server_chan":
      return { title: "自动化通知" };
    case "schedule_next":
      return { delay_seconds: 3600 };
    case "store_state":
      return { keys: [] };
    case "load_state":
    default:
      return {};
  }
}

function ParamField({
  label,
  value,
  onChange,
  type = "text",
  placeholder,
}: {
  label: string;
  value: any;
  onChange: (value: any) => void;
  type?: string;
  placeholder?: string;
}) {
  return (
    <Field label={label}>
      <input
        type={type}
        value={value ?? ""}
        onChange={(event) => onChange(type === "number" ? Number(event.target.value) : event.target.value)}
        placeholder={placeholder}
      />
    </Field>
  );
}

function HandlerParams({
  handler,
  params,
  onChange,
}: {
  handler: string;
  params: any;
  onChange: (params: any) => void;
}) {
  const set = (key: string, value: any) => onChange({ ...params, [key]: value });
  switch (handler) {
    case "send_text":
    case "reply_text":
      return (
        <div className="grid gap-3 sm:grid-cols-2">
          <ParamField label="文本内容" value={params.text} onChange={(value) => set("text", value)} placeholder="支持 {变量名}" />
          <ParamField label="目标对话（可选）" value={params.chat_id} onChange={(value) => set("chat_id", value)} placeholder="默认使用触发消息所在对话" />
        </div>
      );
    case "ai_reply":
      return (
        <div className="grid gap-3 sm:grid-cols-2">
          <ParamField label="提示词" value={params.prompt} onChange={(value) => set("prompt", value)} />
          <ParamField label="最近消息条数" type="number" value={params.recent_limit ?? 8} onChange={(value) => set("recent_limit", value)} />
          <ParamField label="保存到变量（可选）" value={params.store_var} onChange={(value) => set("store_var", value)} placeholder="留空则直接回复" />
        </div>
      );
    case "blacklist_filter":
      return (
        <div className="grid gap-3 sm:grid-cols-2">
          <ParamField label="黑名单关键词" value={fromList(params.keywords)} onChange={(value) => set("keywords", toList(value) || [])} placeholder="广告, 返利" />
          <ParamField label="检查变量（可选）" value={params.source_var} onChange={(value) => set("source_var", value)} placeholder="默认检查消息文本" />
        </div>
      );
    case "delay":
      return <ParamField label="等待秒数" type="number" value={params.seconds ?? 1} onChange={(value) => set("seconds", value)} />;
    case "extract_regex":
      return (
        <div className="grid gap-3 sm:grid-cols-2">
          <ParamField label="正则表达式" value={params.pattern} onChange={(value) => set("pattern", value)} />
          <ParamField label="保存到变量" value={params.var} onChange={(value) => set("var", value)} />
        </div>
      );
    case "relay_message":
      return <RelayTargetsEditor params={params} onChange={onChange} />;
    case "forward":
      return <ParamField label="转发到对话" value={params.chat_id} onChange={(value) => set("chat_id", value)} placeholder="@TargetChat 或数字 ID" />;
    case "random_pick":
      return <ParamField label="随机选项" value={fromList(params.choices)} onChange={(value) => set("choices", toList(value) || [])} placeholder="选项一, 选项二" />;
    case "server_chan":
      return (
        <div className="grid gap-3 sm:grid-cols-2">
          <ParamField label="通知标题" value={params.title} onChange={(value) => set("title", value)} />
          <ParamField label="SendKey（可选）" value={params.send_key} onChange={(value) => set("send_key", value)} />
        </div>
      );
    case "schedule_next":
      return <ParamField label="延后秒数" type="number" value={params.delay_seconds ?? 3600} onChange={(value) => set("delay_seconds", value)} />;
    case "store_state":
      return <ParamField label="保存变量名（可选）" value={fromList(params.keys)} onChange={(value) => set("keys", toList(value) || [])} placeholder="name, answer" />;
    case "load_state":
    default:
      return <p className="text-xs leading-5 text-muted">此动作无需额外参数。</p>;
  }
}

function AutomationEditor({ config, onChange }: Omit<RuleEditorProps, "kind">) {
  const rules = config.rules || [];
  const updateRule = (index: number, patch: any) => {
    const next = clone(config);
    next.rules[index] = { ...next.rules[index], ...patch };
    onChange(next);
  };
  const updateNested = (index: number, key: "triggers" | "handlers", value: any[]) => updateRule(index, { [key]: value });
  const addRule = () =>
    onChange({
      ...clone(config),
      rules: [
        ...rules,
        {
          id: `rule_${rules.length + 1}`,
          enabled: true,
          triggers: [{ type: "message", params: { chat_id: "@YourGroup" } }],
          filters: { text_rule: "contains", text_value: "关键词", ignore_case: true },
          handlers: [{ handler: "send_text", params: { text: "已收到" } }],
          vars: {},
        },
      ],
    });

  return (
    <div className="space-y-4">
      <EditorIntro>
        按“触发条件 → 消息筛选 → 执行动作”搭建自动化流程。每一条规则都可以有多个触发器和动作，拖拽排序暂不需要，按列表顺序执行即可。
      </EditorIntro>
      {rules.map((rule: any, index: number) => (
        <section key={index} className="rounded-xl border border-line bg-slate-50/60 p-4 dark:bg-slate-900/40">
          <div className="mb-4 flex flex-wrap items-center gap-3">
            <Field label="规则名称" className="min-w-48 flex-1">
              <input value={rule.id ?? ""} onChange={(event) => updateRule(index, { id: event.target.value })} placeholder="例如：auto_reply" />
            </Field>
            <label className="mt-6 flex items-center gap-2 text-sm">
              <input type="checkbox" className="accent-emerald-600" checked={rule.enabled !== false} onChange={(event) => updateRule(index, { enabled: event.target.checked })} />
              启用
            </label>
            <Button variant="ghost" size="icon" aria-label="删除规则" disabled={rules.length < 2} onClick={() => onChange({ ...clone(config), rules: rules.filter((_item: any, n: number) => n !== index) })}>
              <Trash2 size={14} />
            </Button>
          </div>

          <div className="space-y-4 border-t border-line pt-4">
            <div>
              <div className="mb-3 flex items-center justify-between"><h4 className="text-sm font-semibold">什么时候触发</h4><Button variant="outline" size="sm" onClick={() => updateNested(index, "triggers", [...(rule.triggers || []), { type: "message", params: {} }])}><Plus size={13} />添加触发器</Button></div>
              <div className="space-y-3">
                {(rule.triggers || []).map((trigger: any, triggerIndex: number) => {
                  const params = trigger.params || {};
                  const setTrigger = (patch: any) => {
                    const triggers = clone(rule.triggers || []);
                    triggers[triggerIndex] = { ...triggers[triggerIndex], ...patch };
                    updateNested(index, "triggers", triggers);
                  };
                  const setParams = (patch: any) => setTrigger({ params: { ...params, ...patch } });
                  return (
                    <div key={triggerIndex} className="rounded-lg border border-line bg-white/80 p-3 dark:bg-slate-800/60">
                      <div className="mb-3 flex items-center gap-2">
                        <select className="!w-44" value={trigger.type} onChange={(event) => setTrigger({ type: event.target.value, params: {} })}>
                          <option value="message">收到消息时</option><option value="timer">定时触发</option><option value="startup">启动时</option>
                        </select>
                        <Button variant="ghost" size="icon" aria-label="删除触发器" disabled={(rule.triggers || []).length < 2} onClick={() => updateNested(index, "triggers", rule.triggers.filter((_item: any, n: number) => n !== triggerIndex))}><Trash2 size={13} /></Button>
                      </div>
                      {trigger.type === "message" && <div className="grid gap-3 sm:grid-cols-2"><ParamField label="单个来源（可选）" value={params.chat_id} onChange={(value) => setParams({ chat_id: value || undefined, chat_ids: undefined })} placeholder="@YourGroup" />
                        <ParamField label="多个来源（用逗号分隔）" value={fromList(params.chat_ids)} onChange={(value) => setParams({ chat_ids: (toList(value) || []).map((id) => /^-?\d+$/.test(id) ? Number(id) : id), chat_id: undefined })} placeholder="@source_a, @source_b, -100123456789" /><ParamField label="发送者（可选）" value={fromList(params.from_user_ids)} onChange={(value) => setParams({ from_user_ids: toList(value) })} placeholder="@admin, 123456" /><CheckField checked={params.reply_to_me} label="只处理回复我的消息" onChange={(value) => setParams({ reply_to_me: value })} /></div>}
                      {trigger.type === "timer" && <div className="grid gap-3 sm:grid-cols-2"><ParamField label="间隔秒数" type="number" value={params.interval_seconds ?? 3600} onChange={(value) => setParams({ interval_seconds: value, cron: undefined })} /><ParamField label="指定对话（可选）" value={params.chat_id} onChange={(value) => setParams({ chat_id: value || undefined })} placeholder="@YourGroup" /><ParamField label="Cron（可选，与间隔二选一）" value={params.cron} onChange={(value) => setParams({ cron: value || undefined, interval_seconds: undefined })} placeholder="0 8 * * *" /></div>}
                      {trigger.type === "startup" && <ParamField label="指定对话（可选）" value={params.chat_id} onChange={(value) => setParams({ chat_id: value || undefined })} placeholder="留空表示启动后立即执行" />}
                    </div>
                  );
                })}
              </div>
            </div>

            <div className="border-t border-line pt-4">
              <h4 className="mb-3 text-sm font-semibold">消息筛选（可选）</h4>
              <div className="grid gap-3 sm:grid-cols-2">
                <ParamField label="来源对话" value={rule.filters?.chat_id} onChange={(value) => updateRule(index, { filters: { ...(rule.filters || {}), chat_id: value || undefined } })} placeholder="留空则使用触发器设置" />
                <ParamField label="发送者" value={fromList(rule.filters?.from_user_ids)} onChange={(value) => updateRule(index, { filters: { ...(rule.filters || {}), from_user_ids: toList(value) } })} placeholder="@admin, 123456" />
                <Field label="文本匹配">
                  <select value={rule.filters?.text_rule || "all"} onChange={(event) => updateRule(index, { filters: { ...(rule.filters || {}), text_rule: event.target.value } })}><option value="all">所有文本</option><option value="contains">包含关键词</option><option value="exact">完全相同</option><option value="regex">正则表达式</option></select>
                </Field>
                <ParamField label="匹配内容" value={rule.filters?.text_value} onChange={(value) => updateRule(index, { filters: { ...(rule.filters || {}), text_value: value || undefined } })} placeholder="例如：签到" />
                <CheckField checked={rule.filters?.ignore_case !== false} label="忽略大小写" onChange={(value) => updateRule(index, { filters: { ...(rule.filters || {}), ignore_case: value } })} />
              </div>
              <RegexTestPanel
                mode={rule.filters?.text_rule || "all"}
                pattern={rule.filters?.text_value || ""}
                ignoreCase={rule.filters?.ignore_case !== false}
              />
            </div>

            <div className="border-t border-line pt-4">
              <div className="mb-3 flex items-center justify-between"><h4 className="text-sm font-semibold">匹配后执行</h4><Button variant="outline" size="sm" onClick={() => updateNested(index, "handlers", [...(rule.handlers || []), { handler: "send_text", params: { text: "" } }])}><Plus size={13} />添加动作</Button></div>
              <div className="space-y-3">
                {(rule.handlers || []).map((item: any, handlerIndex: number) => (
                  <div key={handlerIndex} className="rounded-lg border border-line bg-white/80 p-3 dark:bg-slate-800/60">
                    <div className="mb-3 flex items-center gap-2">
                      <select className="!w-52" value={item.handler} onChange={(event) => { const handlers = clone(rule.handlers || []); handlers[handlerIndex] = { handler: event.target.value, params: paramsFor(event.target.value) }; updateNested(index, "handlers", handlers); }}>
                        {handlerOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                      </select>
                      <Button variant="ghost" size="icon" aria-label="删除动作" disabled={(rule.handlers || []).length < 2} onClick={() => updateNested(index, "handlers", rule.handlers.filter((_item: any, n: number) => n !== handlerIndex))}><Trash2 size={13} /></Button>
                    </div>
                    <HandlerParams handler={item.handler} params={item.params || {}} onChange={(params) => { const handlers = clone(rule.handlers || []); handlers[handlerIndex] = { ...handlers[handlerIndex], params }; updateNested(index, "handlers", handlers); }} />
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>
      ))}
      <Button variant="outline" className="w-full border-dashed" onClick={addRule}><Plus size={15} />添加自动化规则</Button>
    </div>
  );
}

export function RuleEditor({ kind, config, onChange }: RuleEditorProps) {
  return kind === "monitor" ? <MonitorEditor config={config} onChange={onChange} /> : <AutomationEditor config={config} onChange={onChange} />;
}
