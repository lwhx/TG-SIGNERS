"use client";

import { useState } from "react";
import { Eye, ShieldCheck } from "lucide-react";
import { post } from "@/lib/api";
import { Button } from "./ui/button";

type Destination = {
  handler_index: number;
  destination: string;
  mode: string;
  dedup: string;
  would_send: boolean;
  count: number;
  payloads: string[];
  conditional: boolean;
  reason: string;
  note: string;
};
type Preview = {
  rules: Array<{
    rule_id: string;
    matched: boolean;
    reason: string;
    trigger_indices: number[];
    targets: Destination[];
  }>;
  potential_messages: number;
  truncated: boolean;
  warning: string;
};

export function FullRelayPreview({config}: {config: unknown}) {
  const [sourceChat, setSourceChat] = useState("@SourceChannel");
  const [text, setText] = useState("");
  const [senderId, setSenderId] = useState("");
  const [messageId, setMessageId] = useState(1);
  const [isSelf, setIsSelf] = useState(false);
  const [hasMedia, setHasMedia] = useState(false);
  const [replyToMe, setReplyToMe] = useState(false);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true);
    setError("");
    setPreview(null);
    try {
      const configObject = typeof config === "string" ? JSON.parse(config) : config;
      const result = await post<Preview>("/relay/full-preview", {
        config: configObject,
        source_chat: sourceChat,
        source_message: messageId,
        sample: text,
        sender_id: senderId,
        is_self: isSelf,
        has_media: hasMedia,
        reply_to_me: replyToMe,
      });
      setPreview(result);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return <section className="space-y-4 rounded-xl border border-line p-4">
    <div>
      <h3 className="flex items-center gap-2 text-sm font-semibold"><Eye size={16}/>完整转发预览</h3>
      <p className="mt-1 text-xs leading-5 text-muted">根据当前尚未保存的规则模拟消息来源匹配和每个目标的最终发送内容。无任何真实 Telegram 请求或数据库写入。</p>
    </div>
    <div className="grid gap-3 sm:grid-cols-2">
      <label className="text-xs text-muted">模拟来源 Chat ID / @username
        <input className="mt-1 w-full" aria-label="模拟来源" value={sourceChat} maxLength={255} onChange={e=>setSourceChat(e.target.value)}/>
      </label>
      <label className="text-xs text-muted">模拟发送者（可选）
        <input className="mt-1 w-full" aria-label="模拟发送者" value={senderId} maxLength={255} placeholder="@username 或用户 ID" onChange={e=>setSenderId(e.target.value)}/>
      </label>
      <label className="text-xs text-muted">原始消息 ID
        <input className="mt-1 w-full" type="number" min={1} value={messageId} onChange={e=>setMessageId(Number(e.target.value))}/>
      </label>
      <div className="flex flex-wrap items-end gap-x-4 gap-y-2 pb-2">
        <label className="flex items-center gap-2 text-xs"><input className="!w-4" type="checkbox" checked={isSelf} onChange={e=>setIsSelf(e.target.checked)}/>自己发送</label>
        <label className="flex items-center gap-2 text-xs"><input className="!w-4" type="checkbox" checked={hasMedia} onChange={e=>setHasMedia(e.target.checked)}/>含媒体</label>
        <label className="flex items-center gap-2 text-xs"><input className="!w-4" type="checkbox" checked={replyToMe} onChange={e=>setReplyToMe(e.target.checked)}/>回复我</label>
      </div>
      <label className="text-xs text-muted sm:col-span-2">模拟消息正文或媒体说明
        <textarea className="mt-1 w-full" rows={4} maxLength={8000} aria-label="模拟消息内容" value={text} onChange={e=>setText(e.target.value)} placeholder="粘贴需要测试的消息，包含 115 分享链接等"/>
      </label>
    </div>
    <Button type="button" variant="outline" size="sm" disabled={busy || !sourceChat.trim() || messageId < 1} onClick={()=>void run()}>
      <ShieldCheck size={15}/>{busy?"正在模拟…":"预览全部目标"}
    </Button>
    {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-xs text-red-700 dark:bg-red-950/30 dark:text-red-300">{error}</p>}
    {preview && <div className="space-y-3" role="status">
      <p className="text-sm font-medium">理论发送数量：{preview.potential_messages} 条{preview.truncated?"（已触及预览上限）":""}</p>
      {preview.rules.length===0 && <p className="text-xs text-muted">当前配置没有通用消息转发 Handler。</p>}
      {preview.rules.map((rule,index)=><div key={rule.rule_id+"-"+index} className="rounded-lg border border-line p-3">
        <div className="flex flex-wrap justify-between gap-2 text-sm font-medium"><span>{rule.rule_id}</span><span>{rule.matched?"已命中样例":"未命中"}</span></div>
        {rule.reason && <p className="mt-1 text-xs text-muted">{rule.reason}</p>}
        {rule.targets.map((target,i)=><div key={i} className="mt-3 rounded-md bg-slate-50 p-3 text-xs dark:bg-slate-800">
          <div className="flex flex-wrap justify-between gap-2"><strong>{target.destination} · {target.mode}</strong><span>{target.would_send?"预计发送 "+target.count+" 条":"不会发送"}</span></div>
          <p className="mt-1 text-muted">去重策略：{target.dedup}{target.conditional?" · 依赖前置动作执行结果":""}</p>
          {(target.reason || target.note) && <p className="mt-2 text-muted">{target.reason||target.note}</p>}
          {target.payloads.map((payload,j)=><pre key={j} className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap break-all rounded bg-white p-2 text-xs dark:bg-slate-900">{payload}</pre>)}
        </div>)}
      </div>)}
      <p className="text-xs leading-5 text-muted">{preview.warning}</p>
    </div>}
  </section>;
}
