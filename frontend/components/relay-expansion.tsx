"use client";

import { useEffect, useMemo, useState } from "react";
import { Archive, Images, Play, RefreshCw, ShieldCheck, Square } from "lucide-react";
import { toast } from "sonner";
import { Account, api, post, Task } from "@/lib/api";
import { pollVisible } from "@/lib/polling";
import { Button } from "./ui/button";

type BotRow = {
  destination: string; fingerprint: string; account_id: number; task_id: number|null;
  bot_chat: number; sent_message: number; reply_message: number|null;
  deadline: number; status: string; created_at: number; updated_at: number;
};
type BotPage = {items: BotRow[];total:number;page:number;page_size:number};
type AlbumStats = {sent:number;failed:number;sending:number;uncertain:number};
type Job = {
  id:number; task_id:number; account_id:number; source_chat:string;
  before_id:number|null; next_before_id:number|null; batch_size:number;
  status:string; scanned:number; matched:number; last_error:string;
  created_at:string; updated_at:string;
};
type Plan = {
  task_id:number;account_id:number;source_chat:string;limit:number;
  before_id:number|null;rules:string[];target_count:number;dry_run:boolean;warning:string;
};
const BOT_NAMES:Record<string,string> = {
  pending:"等待 Bot 回复",succeeded:"Bot 确认成功",failed:"Bot 确认失败",
  unclassified:"已收到回复，结果未知",expired:"等待超时，未确认",
};
const JOB_NAMES:Record<string,string> = {
  queued:"排队中",running:"运行中",completed:"本批完成",
  interrupted:"已中断",failed:"本批出错",cancel_requested:"正在停止",
};

function sourcesFor(task?:Task):string[] {
  if(!task || task.kind!=="automation")return [];
  const set = new Set<string>();
  for(const rule of task.config.rules || []) {
    if(rule.enabled===false || !(rule.handlers||[]).some((h:{handler:string})=>h.handler==="relay_message"))continue;
    for(const trigger of rule.triggers || []) {
      if(trigger.type!=="message")continue;
      const options = [...(trigger.params?.chat_ids || [])];
      if(trigger.params?.chat_id != null)options.push(trigger.params.chat_id);
      for(const source of options) if(String(source).trim())set.add(String(source));
    }
  }
  return [...set];
}

export function RelayExpansion({accounts,tasks}: {accounts:Account[];tasks:Task[]}) {
  const relayTasks = useMemo(()=>tasks.filter(t=>t.kind==="automation"),[tasks]);
  const [accountId,setAccountId] = useState("");
  const [taskId,setTaskId] = useState("");
  const [botStatus,setBotStatus] = useState("");
  const [botPage,setBotPage] = useState(1);
  const [botResults,setBotResults] = useState<BotPage|null>(null);
  const [albumStats,setAlbumStats] = useState<AlbumStats|null>(null);
  const [jobs,setJobs] = useState<Job[]>([]);
  const [revision,setRevision] = useState(0);
  const [readError,setReadError] = useState("");
  const [source,setSource] = useState("");
  const [limit,setLimit] = useState(20);
  const [beforeId,setBeforeId] = useState("");
  const [plan,setPlan] = useState<Plan|null>(null);
  const [busy,setBusy] = useState("");
  const currentTask = relayTasks.find(t=>t.id===Number(taskId));
  const choices = sourcesFor(currentTask);
  const selectedTaskId = taskId?Number(taskId):undefined;

  useEffect(()=>{setPlan(null);},[source,limit,beforeId,taskId]);
  useEffect(()=>{
    const query = new URLSearchParams({page:String(botPage),page_size:"20"});
    if(accountId)query.set("account_id",accountId);
    if(taskId)query.set("task_id",taskId);
    if(botStatus)query.set("status",botStatus);
    const statsQuery = new URLSearchParams();
    if(accountId)statsQuery.set("account_id",accountId);
    if(taskId)statsQuery.set("task_id",taskId);
    return pollVisible(async signal=>{
      try {
        const [receipts,albums,backfills]=await Promise.all([
          api<BotPage>("/relay/bot-results?"+query,{signal}),
          api<AlbumStats>("/relay/media-groups/stats?"+statsQuery,{signal}),
          api<{items:Job[]}>("/relay/backfill/jobs?limit=20"+(taskId?"&task_id="+taskId:""),{signal}),
        ]);
        if(!signal.aborted) {
          setBotResults(receipts);setAlbumStats(albums);setJobs(backfills.items);setReadError("");
        }
      } catch(err) {if(!signal.aborted)setReadError((err as Error).message);}
    },15000);
  },[accountId,taskId,botStatus,botPage,revision]);

  const inputPayload = ()=>({
    task_id:Number(taskId),source_chat:source,limit,
    before_id:beforeId?Number(beforeId):null,
  });
  const preview = async ()=>{
    setBusy("preview");
    try {
      const result=await post<Plan>("/relay/backfill/preview",inputPayload());
      setPlan(result);
      toast.success("补抓配置检查通过；尚未读取或转发消息");
    }catch(err){setPlan(null);toast.error((err as Error).message);}
    finally{setBusy("");}
  };
  const start = async ()=>{
    if(!plan)return;
    if(!window.confirm(
      "启动历史消息补抓？\n\n来源："+plan.source_chat+
      "\n最多读取："+plan.limit+" 条\n匹配规则："+plan.rules.join("、")+
      "\n此操作会实际向目标转发未投递过的历史消息；不自动重试未知投递结果。"
    ))return;
    setBusy("start");
    try{
      await post("/relay/backfill/start",{...inputPayload(),confirmation:"BACKFILL"});
      setPlan(null);
      setRevision(x=>x+1);
      toast.success("已创建历史补抓任务，可在下方查看状态");
    }catch(err){toast.error((err as Error).message);}
    finally{setBusy("");}
  };
  const cancel=async (id:number)=>{
    if(!window.confirm("停止本批次？正在发送的消息可能无法撤回，状态不明的投递不会自动重试。"))return;
    try{
      await post("/relay/backfill/jobs/"+id+"/cancel",{});
      toast.success("已发送停止请求");
      setRevision(x=>x+1);
    }catch(err){toast.error((err as Error).message);}
  };
  const nameFor=(id:number|null)=>
    id==null?"旧记录":tasks.find(t=>t.id===id)?.name || "任务 #"+id;
  return <div className="space-y-5">
    <section className="card space-y-4 p-5">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 className="font-semibold">P7 · 转发增强</h2>
          <p className="mt-1 text-xs leading-5 text-muted">115 Bot 结果独立于 Telegram 送达记录；媒体组和历史补抓仍复用 P4 去重账本。</p>
        </div>
        <Button size="sm" variant="outline" onClick={()=>setRevision(x=>x+1)}><RefreshCw size={14}/>刷新</Button>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-xs text-muted">Telegram 账号
          <select className="mt-1" aria-label="增强功能账号" value={accountId} onChange={e=>{setAccountId(e.target.value);setTaskId("");setSource("");setBotPage(1);}}>
            <option value="">全部账号</option>
            {accounts.map(a=><option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </label>
        <label className="text-xs text-muted">自动化任务
          <select className="mt-1" aria-label="增强功能任务" value={taskId} onChange={e=>{setTaskId(e.target.value);setSource("");setBotPage(1);}}>
            <option value="">全部自动化任务</option>
            {relayTasks.filter(t=>!accountId||t.account_id===Number(accountId)).map(t=>
              <option key={t.id} value={t.id}>{t.name}{t.enabled?"":"（已停用）"}</option>)}
          </select>
        </label>
      </div>
      {readError && <p role="alert" className="text-sm text-red-600">{readError}</p>}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {([["整组已发送",albumStats?.sent],["整组失败",albumStats?.failed],
        ["整组结果待查",albumStats?.uncertain],["整组发送中",albumStats?.sending]] as const)
          .map(([label,value])=><div key={label} className="rounded-lg border border-line p-3">
            <p className="text-xs text-muted">{label}</p><p className="mt-1 text-xl font-semibold tabular-nums">{value??"—"}</p>
          </div>)}
      </div>
      <p className="text-xs text-muted">媒体组需在「自动化与转发 → 编辑任务 → 转发目标」中选择“整组发送相册”，默认仍为逐条处理。整组发送结果不确定时必须人工核查。</p>
    </section>
    <section className="card space-y-3 p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 font-semibold"><Archive size={17}/>115 Bot 结果追踪</h3>
        <label className="text-xs text-muted">回执状态
          <select className="ml-2" aria-label="回执状态筛选" value={botStatus} onChange={e=>{setBotStatus(e.target.value);setBotPage(1);}}>
            <option value="">全部</option>
            {Object.entries(BOT_NAMES).map(([k,v])=><option key={k} value={k}>{v}</option>)}
          </select>
        </label>
      </div>
      <p className="text-xs leading-5 text-muted">仅关联 Bot 明确回复已发送消息的结果。未回复、无关联回复或等待超时，不代表转存失败或成功。Bot 不回复原消息时需要手工检查。</p>
      {!botResults?<p className="text-sm text-muted">正在读取结果…</p>:
      botResults.total===0?<p className="text-sm text-muted">暂无 Bot 回执记录。请在转发目标中启用“追踪目标 Bot 的处理结果”。</p>:
      <div className="overflow-x-auto"><table className="w-full min-w-[590px] text-left text-sm">
        <thead><tr className="border-b border-line text-xs text-muted">
          <th className="py-2">任务</th><th>目标 Bot</th><th>Telegram 消息</th><th>Bot 回执</th><th>结果</th>
        </tr></thead>
        <tbody>{botResults.items.map(item=><tr key={item.destination+item.fingerprint} className="border-b border-line">
          <td className="py-3">{nameFor(item.task_id)}</td>
          <td className="font-mono text-xs">{item.destination}</td>
          <td className="font-mono text-xs">{item.sent_message}</td>
          <td className="font-mono text-xs">{item.reply_message??"—"}</td>
          <td className="text-xs">{BOT_NAMES[item.status]||item.status}</td>
        </tr>)}</tbody>
      </table></div>}
      {botResults && botResults.total>20&&<div className="flex items-center justify-end gap-2">
        <Button variant="outline" size="sm" disabled={botPage<=1} onClick={()=>setBotPage(p=>p-1)}>上一页</Button>
        <span className="text-xs text-muted">第 {botPage} 页 · {botResults.total} 条</span>
        <Button variant="outline" size="sm" disabled={botPage*20>=botResults.total} onClick={()=>setBotPage(p=>p+1)}>下一页</Button>
      </div>}
    </section>
    <section className="card space-y-4 p-5">
      <div>
        <h3 className="flex items-center gap-2 font-semibold"><Play size={17}/>手动历史消息补抓</h3>
        <p className="mt-1 text-xs leading-5 text-muted">只允许已启用、且使用纯通用转发动作的任务。每批最多 50 条，按旧到新处理；不能自动恢复中断任务。</p>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <label className="text-xs text-muted">授权来源频道 / 群组
          <select className="mt-1" aria-label="补抓消息来源" value={source} onChange={e=>setSource(e.target.value)}>
            <option value="">选择任务中声明的来源</option>
            {choices.map(value=><option key={value} value={value}>{value}</option>)}
          </select>
        </label>
        <label className="text-xs text-muted">每批最大读取条数
          <input className="mt-1" type="number" min={1} max={50} aria-label="补抓条数" value={limit}
            onChange={e=>setLimit(Number(e.target.value))}/>
        </label>
        <label className="text-xs text-muted">早于消息 ID（可选）
          <input className="mt-1" type="number" min={1} aria-label="补抓起始游标"
            placeholder="留空从最新历史开始" value={beforeId}
            onChange={e=>setBeforeId(e.target.value)}/>
        </label>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" variant="outline" disabled={busy!==""||!selectedTaskId||!source||!currentTask?.enabled||limit<1||limit>50}
          onClick={()=>void preview()}><ShieldCheck size={14}/>检查补抓范围</Button>
        <Button type="button" size="sm" disabled={!plan||busy!==""} onClick={()=>void start()}>
          <Play size={14}/>确认启动该批次
        </Button>
      </div>
      {plan&&<div className="rounded-lg border border-line p-3 text-xs leading-6">
        <strong>预检通过：</strong>来源 {plan.source_chat}，最多 {plan.limit} 条，
        命中规则 {plan.rules.join("、")}，转发目标数 {plan.target_count}。
        <p className="text-muted">{plan.warning}</p>
      </div>}
      <div className="border-t border-line pt-4">
        <h4 className="mb-3 flex items-center gap-2 text-sm font-semibold"><Images size={15}/>最近补抓批次</h4>
        {jobs.length===0?<p className="text-xs text-muted">尚无手动补抓批次。</p>:
        <div className="space-y-2">{jobs.map(job=><div key={job.id} className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-line p-3 text-xs">
          <div className="space-y-1">
            <p className="font-semibold">#{job.id} · {nameFor(job.task_id)} · {JOB_NAMES[job.status]||job.status}</p>
            <p className="text-muted">{job.source_chat} · 扫描 {job.scanned}/{job.batch_size} · 命中 {job.matched} 条{job.last_error?" · "+job.last_error:""}</p>
            <p className="text-muted">{job.created_at?.slice(0,19).replace("T"," ")}</p>
          </div>
          <div className="flex gap-2">
            {job.status==="completed"&&job.next_before_id&&<Button variant="outline" size="sm" onClick={()=>{
              setTaskId(String(job.task_id));setSource(job.source_chat);setBeforeId(String(job.next_before_id));
              setLimit(job.batch_size);setPlan(null);
              toast.info("已填充下一批游标，请重新预览后执行");
            }}>继续更早历史</Button>}
            {["running","queued"].includes(job.status)&&<Button variant="outline" size="sm" onClick={()=>void cancel(job.id)}>
              <Square size={13}/>停止
            </Button>}
          </div>
        </div>)}</div>}
      </div>
    </section>
  </div>;
}
