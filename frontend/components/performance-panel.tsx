"use client";

import { useEffect, useState } from "react";
import { Activity, Gauge, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { api, post } from "@/lib/api";
import { Button } from "./ui/button";

type Database = {
  present:boolean; bytes:number; wal_bytes:number;
  logical_bytes?:number; p50_ms:number|null;p95_ms:number|null;
};
type Sample = {
  measured_at:string;architecture:string;dashboard:Database;relay:Database;
  disk_free_mb:number;disk_total_mb:number;
  process_rss_peak_mb:number;process_cpu_seconds:number;probe_ms:number;
};
type Migrations = {
  current:number;supported:number;
  migrations:{version:number;description:string;applied_at:string}[];
};
const bytes = (v:number)=> (v/1048576).toFixed(2)+" MiB";
const time = (value:string)=>new Date(value).toLocaleString("zh-CN",{hour12:false});

export function PerformancePanel() {
  const [current,setCurrent] = useState<Sample|null>(null);
  const [samples,setSamples] = useState<Sample[]>([]);
  const [migrations,setMigrations] = useState<Migrations|null>(null);
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState("");

  const load = async () => {
    try {
      const [report,schema]=await Promise.all([
        api<{current:Sample;history:Sample[]}>("/maintenance/performance"),
        api<Migrations>("/maintenance/performance/schema"),
      ]);
      setCurrent(report.current);
      setSamples(report.history);
      setMigrations(schema);
      setError("");
    }catch(err){setError((err as Error).message);}
  };
  useEffect(()=>{void load();},[]);
  const measure = async () => {
    setBusy(true);
    try {
      await post("/maintenance/performance/measure");
      await load();
      toast.success("已完成本次性能采样并保留最近 30 次记录");
    }catch(err){toast.error((err as Error).message);}
    finally {setBusy(false);}
  };
  const rows: Array<[string,Database|undefined]> = [
    ["Dashboard SQLite",current?.dashboard],
    ["转发账本 SQLite",current?.relay],
  ];
  return <section className="card space-y-5 p-6">
    <div className="flex flex-wrap items-start justify-between gap-2">
      <div>
        <h2 className="flex items-center gap-2 font-semibold"><Gauge size={18}/>运行性能基线</h2>
        <p className="mt-2 text-xs leading-6 text-muted">
          只做短时只读 SQLite 查询、磁盘与进程指标采样，不获取 Telegram 消息内容，也不会发送消息。
          指标用于不同版本和不同服务器间的趋势比较，不代表真实 Telegram 网络延迟。
        </p>
      </div>
      <div className="flex gap-2">
        <Button type="button" size="sm" variant="outline" onClick={()=>void load()}><RefreshCw size={14}/>刷新</Button>
        <Button type="button" size="sm" disabled={busy} onClick={()=>void measure()}>
          <Activity size={14}/>{busy?"采样中…":"记录一次性能采样"}
        </Button>
      </div>
    </div>
    {error && <p role="alert" className="text-xs text-red-600">{error}</p>}
    {current && <>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {([
          ["运行架构",current.architecture],
          ["可用磁盘",current.disk_free_mb+" MB"],
          ["进程峰值内存",current.process_rss_peak_mb+" MB"],
          ["本次测量耗时",current.probe_ms+" ms"],
        ] as const).map(([label,value])=><div key={label} className="rounded-lg border border-line p-3">
          <p className="text-xs text-muted">{label}</p>
          <p className="mt-2 text-sm font-semibold tabular-nums">{value}</p>
        </div>)}
      </div>
      <div className="overflow-x-auto"><table className="w-full min-w-[460px] text-sm">
        <thead><tr className="border-b border-line text-left text-xs text-muted">
          <th className="py-2">数据库</th><th>文件大小</th><th>WAL</th><th>p50</th><th>p95 / 最大</th>
        </tr></thead>
        <tbody>{rows.map(([name,db])=><tr key={name} className="border-b border-line">
          <td className="py-3 text-xs font-semibold">{name}</td>
          <td className="text-xs">{db?.present?bytes(db.bytes):"不存在"}</td>
          <td className="text-xs">{db?.present?bytes(db.wal_bytes):"—"}</td>
          <td className="text-xs">{db?.p50_ms??"—"} ms</td>
          <td className="text-xs">{db?.p95_ms??"—"} ms</td>
        </tr>)}</tbody>
      </table></div>
      <p className="text-xs text-muted">测量时间：{time(current.measured_at)}。p50 为采样中位数；p95 为短样本的保守最大值，不能当作正式压测结果。</p>
    </>}
    <div className="border-t border-line pt-4">
      <h3 className="text-sm font-semibold">数据库迁移版本</h3>
      <p className="mt-2 text-xs text-muted">{migrations
        ? "当前版本 v"+migrations.current+" · 应用支持至 v"+migrations.supported+" · 已记录 "+migrations.migrations.length+" 次增量迁移"
        :"读取中…"}</p>
      {!!migrations?.migrations.length && <div className="mt-2 space-y-1">
        {migrations.migrations.map(m=><p className="text-xs text-muted" key={m.version}>
          v{m.version} · {m.description}
        </p>)}
      </div>}
    </div>
    <div className="border-t border-line pt-4">
      <h3 className="text-sm font-semibold">最近性能采样（最多 30 次）</h3>
      {samples.length===0?<p className="mt-2 text-xs text-muted">尚未保存测量结果。</p>:
      <div className="mt-3 max-h-52 overflow-y-auto">
        {[...samples].reverse().map((s,i)=><div key={s.measured_at+"-"+i} className="flex flex-wrap justify-between gap-2 border-b border-line py-2 text-xs">
          <span>{time(s.measured_at)}</span>
          <span className="text-muted">DB p50 {s.dashboard.p50_ms??"—"} ms · 内存峰值 {s.process_rss_peak_mb} MB · 余量 {s.disk_free_mb} MB</span>
        </div>)}
      </div>}
    </div>
  </section>;
}
