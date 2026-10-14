"use client";

import { useEffect, useState } from "react";
import { ArchiveRestore, Download, HardDriveDownload, ShieldCheck, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { api, post } from "@/lib/api";
import { Button } from "./ui/button";

type Backup = {id: string; size: number; created_at: string};
type State = {
  pending: {id:string;created_at:string}|null;
  last: {id:string;status:string;time:string}|null;
};
const empty: State = {pending:null,last:null};
const fmt = (date:string) => new Date(date).toLocaleString("zh-CN",{hour12:false});

export function BackupManagement() {
  const [items,setItems] = useState<Backup[]>([]);
  const [state,setState] = useState<State>(empty);
  const [password,setPassword] = useState("");
  const [busy,setBusy] = useState("");
  const [error,setError] = useState("");

  const reload = async () => {
    try {
      const [listed,status] = await Promise.all([
        api<{items:Backup[]}>("/maintenance/backups"),
        api<State>("/maintenance/backups/restore-status"),
      ]);
      setItems(listed.items);
      setState(status);
      setError("");
    } catch(err) {
      setError((err as Error).message);
    }
  };
  useEffect(() => {void reload();},[]);

  const action = async (label:string, callback:()=>Promise<void>) => {
    if(busy)return;
    setBusy(label);
    try {await callback();await reload();}
    catch(err){toast.error((err as Error).message);}
    finally {setBusy("");}
  };
  const checkPassphrase = () => {
    if(password.length<16 || password.length>1024) {
      toast.error("请填写至少 16 字符的备份加密密码");
      return false;
    }
    return true;
  };
  const create = () => {
    if(!checkPassphrase())return;
    void action("create",async()=>{
      await post("/maintenance/backups",{passphrase:password});
      setPassword("");
      toast.success("已生成加密备份；建议下载至安全的离线存储");
    });
  };
  const verify = (id:string) => {
    if(!checkPassphrase())return;
    void action("verify-"+id,async()=>{
      const info=await post<{files:number}>("/maintenance/backups/"+id+"/verify",{passphrase:password});
      toast.success("文件、哈希与 SQLite 校验通过，共 "+info.files+" 个文件");
    });
  };
  const restore = (id:string) => {
    if(!checkPassphrase())return;
    if(!window.confirm(
      "危险操作：确定把这份备份恢复到本容器吗？\n\n"
      +"本操作会暂存并校验数据，但只有手动重启 Docker 容器后才会正式替换数据库和本地密钥。"
      +" 当前登录可能在重启后失效；请预先下载当前完整备份并确保密码已离线保存。"
    ))return;
    void action("restore-"+id,async()=>{
      await post("/maintenance/backups/"+id+"/restore",{
        passphrase:password,confirmation:"RESTORE",
      });
      setPassword("");
      toast.success("已暂存恢复数据；请人工重启容器以执行冷启动恢复");
    });
  };
  const remove = (id:string) => {
    if(!window.confirm("永久删除本地加密备份 "+id.slice(0,8)+"？此操作无法撤销。"))return;
    void action("delete-"+id,async()=>{
      await api("/maintenance/backups/"+id,{
        method:"DELETE",body:JSON.stringify({confirmation:"DELETE"}),
      });
      toast.success("已删除本地备份");
    });
  };
  return <section className="card space-y-5 p-6">
    <div>
      <h2 className="flex items-center gap-2 font-semibold"><ArchiveRestore size={18}/>加密备份与恢复</h2>
      <p className="mt-2 text-xs leading-6 text-muted">
        每份备份使用独立密码加密，包含 Dashboard、转发账本、Telegram 会话加密密钥及应用数据。
        备份最多保留 12 份本地文件；必须妥善保存密码，丢失密码无法恢复。
      </p>
    </div>
    <div className="grid gap-3 sm:grid-cols-[1fr_auto] sm:items-end">
      <label className="text-xs text-muted">加密备份密码（至少 16 字符；不会保存在服务器设置中）
        <input type="password" autoComplete="new-password" className="mt-1 w-full"
          aria-label="备份加密密码" value={password} maxLength={1024}
          onChange={e=>setPassword(e.target.value)} placeholder="用于创建、验证及恢复当前备份"/>
      </label>
      <Button type="button" size="sm" disabled={Boolean(busy)||password.length<16} onClick={create}>
        <HardDriveDownload size={15}/>{busy==="create"?"正在生成…":"创建加密备份"}
      </Button>
    </div>
    {error && <p role="alert" className="text-xs text-red-600">{error}</p>}
    {state.pending && <div className="rounded-lg border border-amber-300 p-3 text-xs leading-6">
      <strong>待冷启动恢复：</strong>{state.pending.id.slice(0,12)} · {fmt(state.pending.created_at)}
      <p>必须人工执行 Docker 容器重启。请勿在恢复完成前重新发起其他恢复；系统会在启动时保留原数据回退副本。</p>
    </div>}
    {state.last && <p className="text-xs text-muted">
      最近恢复：{state.last.status==="completed"?"已完成":"已回滚"} ·
      {" "+fmt(state.last.time)} · {state.last.id.slice(0,12)}
    </p>}
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold">本地备份 ({items.length}/12)</h3>
        <Button type="button" size="sm" variant="outline" onClick={()=>void reload()}>刷新列表</Button>
      </div>
      {items.length===0?<p className="text-xs text-muted">尚无加密备份。首次创建后建议立即下载至其他安全存储位置。</p>:
      items.map(item=><div key={item.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-line p-3">
        <div className="min-w-0 text-xs">
          <p className="font-semibold">{fmt(item.created_at)}</p>
          <p className="mt-1 font-mono text-muted">{item.id.slice(0,12)} · {(item.size/1048576).toFixed(2)} MiB</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <a
            href={"/api/maintenance/backups/"+item.id+"/download"}
            className="inline-flex items-center gap-1 rounded-md border border-line px-3 py-2 text-xs hover:bg-slate-50"
            download={"tg-signer-"+item.id+".tgsb"}
          ><Download size={13}/>下载</a>
          <Button size="sm" variant="outline" disabled={Boolean(busy)||password.length<16}
            onClick={()=>verify(item.id)}><ShieldCheck size={13}/>校验</Button>
          <Button size="sm" variant="outline" disabled={Boolean(busy)||password.length<16||Boolean(state.pending)}
            onClick={()=>restore(item.id)}><ArchiveRestore size={13}/>暂存恢复</Button>
          <Button size="sm" variant="ghost" disabled={Boolean(busy)||state.pending?.id===item.id}
            onClick={()=>remove(item.id)}><Trash2 size={13}/>删除</Button>
        </div>
      </div>)}
    </div>
    <p className="text-xs leading-6 text-muted">
      <strong>安全提醒：</strong>加密备份含账户会话及密钥，不要公开分享；离线下载后建议单独保管。
      数据恢复只在冷启动执行，不在运行中热替换 SQLite。单库快照各自一致，不保证不同 SQLite 文件跨库瞬时一致。
    </p>
  </section>;
}
