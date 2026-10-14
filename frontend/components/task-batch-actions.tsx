"use client";

import { useState } from "react";
import { toast } from "sonner";
import { post, Task } from "@/lib/api";
import { Button } from "./ui/button";

type Operation = "enable" | "disable" | "move_group" | "delete";
type Preview = {count: number; items: Array<{name: string}>};
type Result = {count: number};

export function TaskBatchActions({selected, groups, onDone}: {
  selected: Task[]; groups: string[]; onDone: ()=>void;
}) {
  const [operation, setOperation] = useState<Operation>("disable");
  const [group, setGroup] = useState("");
  const [busy, setBusy] = useState(false);
  const execute = async () => {
    if (!selected.length || busy) return;
    setBusy(true);
    try {
      const payload = {
        ids: selected.map(t=>t.id), operation,
        group_name: group, dry_run: true,
      };
      const preview = await post<Preview>("/tasks/batch", payload);
      const labels: Record<Operation,string> = {
        enable: "启用", disable:"停用", move_group:"移动分组", delete:"永久删除",
      };
      const names = preview.items.slice(0,8).map(t=>t.name).join("、");
      if (!window.confirm("确认"+labels[operation]+" "+preview.count+" 个任务？\n"+names+
        (preview.count>8?"…":"")+
        (operation==="delete"?"\n删除任务会删除其配置版本，但保留历史执行记录。":"" )+
        (operation==="enable"?"\n启用监听任务将立即启动；签到任务会重新排期。":""))) return;
      const result = await post<Result>("/tasks/batch", {
        ...payload,
        dry_run: false,
        confirmation: operation==="delete"?"DELETE":undefined,
      });
      toast.success("批量操作完成，共修改 "+result.count+" 个任务");
      onDone();
    } catch (err) {
      toast.error((err as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return <div className="flex flex-wrap items-end gap-2 border-y border-line bg-slate-50/60 px-5 py-3 dark:bg-slate-900/30">
    <span className="pb-2 text-xs font-medium">已选择 {selected.length} 个（每批最多 50 个）</span>
    <label className="text-xs text-muted">操作
      <select aria-label="批量操作" className="mt-1" value={operation} onChange={e=>setOperation(e.target.value as Operation)}>
        <option value="disable">批量停用</option>
        <option value="enable">批量启用</option>
        <option value="move_group">移动到分组</option>
        <option value="delete">删除选中任务</option>
      </select>
    </label>
    {operation==="move_group" && <label className="text-xs text-muted">目标分组（留空移出分组）
      <input className="mt-1" aria-label="目标管理分组" list="batch-groups" maxLength={60}
        value={group} onChange={e=>setGroup(e.target.value)}/>
      <datalist id="batch-groups">{groups.map(g=><option key={g} value={g}/>)}</datalist>
    </label>}
    <Button type="button" size="sm" onClick={()=>void execute()} disabled={busy || !selected.length}>
      {busy?"正在处理…":"预览并执行"}
    </Button>
    <Button type="button" size="sm" variant="ghost" onClick={onDone}>清空选择</Button>
  </div>;
}
