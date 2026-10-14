"use client";

import { useEffect, useMemo, useState } from "react";
import { RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { api, post, Task } from "@/lib/api";
import { Button } from "./ui/button";
import { Dialog, DialogContent } from "./ui/dialog";

type VersionSummary = {
  version: number; operation: string; created_at: string;
  digest: string; name: string; group_name: string; enabled: boolean;
};
type VersionList = {
  task_id: number; current_version: number;
  items: VersionSummary[]; current: Record<string, unknown>;
};
type VersionDetail = { version: number; operation: string; digest: string; snapshot: Record<string, unknown> };

function changedPaths(before: unknown, after: unknown, path = "", rows: string[] = []): string[] {
  if (rows.length > 80 || JSON.stringify(before) === JSON.stringify(after)) return rows;
  if (before && after && typeof before === "object" && typeof after === "object") {
    const a = before as Record<string, unknown>;
    const b = after as Record<string, unknown>;
    for (const key of new Set([...Object.keys(a), ...Object.keys(b)])) {
      changedPaths(a[key], b[key], path ? path + "." + key : key, rows);
      if (rows.length > 80) break;
    }
  } else {
    rows.push(path || "配置");
  }
  return rows;
}

export function TaskVersions({task, onClose, onChanged}: {
  task: Task; onClose: () => void; onChanged: () => void;
}) {
  const [versions, setVersions] = useState<VersionList | null>(null);
  const [detail, setDetail] = useState<VersionDetail | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    api<VersionList>("/tasks/"+task.id+"/versions", {signal: controller.signal})
      .then(list => {
        if (controller.signal.aborted) return;
        setVersions(list);
        setSelected(old => old !== null && list.items.some(v=>v.version===old) ? old : list.items[0]?.version ?? null);
        setError("");
      }).catch(err => {
        if (!controller.signal.aborted) setError((err as Error).message);
      });
    return ()=>controller.abort();
  },[task.id,revision]);
  useEffect(() => {
    if (selected===null) return;
    const controller = new AbortController();
    setDetail(null);
    api<VersionDetail>("/tasks/"+task.id+"/versions/"+selected,{signal:controller.signal})
      .then(data=>{if(!controller.signal.aborted)setDetail(data);})
      .catch(err=>{if(!controller.signal.aborted)setError((err as Error).message);});
    return ()=>controller.abort();
  },[task.id,selected,revision]);
  const changes = useMemo(() => detail && versions
    ? changedPaths(detail.snapshot, versions.current) : [], [detail,versions]);
  const rollback = async () => {
    if (!versions || !detail || busy) return;
    if (!window.confirm("将任务「"+task.name+"」恢复到 V"+detail.version+"？\n将保留当前启用/停用状态；当前配置也会以新版本留存。")) return;
    setBusy(true);
    try {
      await post("/tasks/"+task.id+"/versions/"+detail.version+"/restore", {
        expected_version: versions.current_version,
      });
      toast.success("已从 V"+detail.version+" 恢复，并生成新的版本记录");
      onChanged();
      setRevision(n=>n+1);
    } catch(err) {toast.error((err as Error).message);}
    finally {setBusy(false);}
  };
  return <Dialog open onOpenChange={open=>!open&&onClose()}>
    <DialogContent className="max-w-4xl" title={"配置版本 · "+task.name} description="每次新建、修改及批量更改都会记录快照，可对比并恢复历史设置。">
      <div className="space-y-4">
        {error && <p role="alert" className="text-sm text-red-600">{error}</p>}
        {!versions && !error && <p className="text-sm text-muted">正在读取历史版本…</p>}
        {versions && <>
          <div className="flex flex-wrap justify-between gap-2 text-xs text-muted">
            <span>当前版本 V{versions.current_version}</span>
            <span>最近 {versions.items.length} 个版本（仅保存任务配置，不含账号 Session）</span>
          </div>
          <div className="flex max-h-44 flex-wrap gap-2 overflow-y-auto">
            {versions.items.map(v=><Button key={v.version} size="sm" variant={selected===v.version?"default":"outline"} onClick={()=>setSelected(v.version)}>
              V{v.version} · {v.operation}
            </Button>)}
          </div>
          {detail && <div className="space-y-3">
            <p className="text-sm">
              V{detail.version} → 当前：{changes.length ? changes.length+" 处配置差异" : "配置一致"}
            </p>
            {!!changes.length && <p className="max-h-20 overflow-y-auto break-all text-xs text-muted">{changes.slice(0,30).join("、")}{changes.length>30?"…":""}</p>}
            <div className="grid gap-3 sm:grid-cols-2">
              <section><h4 className="mb-2 text-xs font-semibold">历史版本 V{detail.version}</h4>
                <pre className="h-64 overflow-auto rounded-md bg-slate-50 p-3 text-xs dark:bg-slate-900">{JSON.stringify(detail.snapshot,null,2)}</pre>
              </section>
              <section><h4 className="mb-2 text-xs font-semibold">当前任务配置</h4>
                <pre className="h-64 overflow-auto rounded-md bg-slate-50 p-3 text-xs dark:bg-slate-900">{JSON.stringify(versions.current,null,2)}</pre>
              </section>
            </div>
            <div className="flex flex-wrap justify-between gap-2 border-t border-line pt-3">
              <p className="max-w-lg text-xs text-muted">恢复会重新校验配置并重新装载运行任务，不会恢复历史版本的启用状态；不会删除转发去重记录。</p>
              <Button size="sm" onClick={()=>void rollback()} disabled={busy || detail.version===versions.current_version || !changes.length}>
                <RotateCcw size={14}/>{busy?"恢复中…":"恢复此版本"}
              </Button>
            </div>
          </div>}
        </>}
      </div>
    </DialogContent>
  </Dialog>;
}
