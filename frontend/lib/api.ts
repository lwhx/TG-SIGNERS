export async function api<T = any>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const response = await fetch("/api" + path, {
    ...init,
    credentials: "same-origin",
    headers: {
      ...(init.body && !(init.body instanceof FormData)
        ? { "Content-Type": "application/json" }
        : {}),
      ...init.headers,
    },
  });
  if (!response.ok) {
    const data = await response
      .json()
      .catch(() => ({ detail: "服务暂时不可用" }));
    if (response.status === 401 && !path.startsWith("/panel/"))
      window.dispatchEvent(new Event("session-expired"));
    const detail = Array.isArray(data.detail)
      ? data.detail
          .map((v: any) => `${v.loc.slice(1).join(".")}: ${v.msg}`)
          .join("\n")
      : data.detail;
    throw new Error(detail || `请求失败 (${response.status})`);
  }
  return response.json();
}
export const post = <T = any>(path: string, data?: any) =>
  api<T>(path, {
    method: "POST",
    ...(data !== undefined ? { body: JSON.stringify(data) } : {}),
  });
export interface Account {
  id: number;
  name: string;
  phone: string;
  username: string;
  user_id: string;
  avatar: string;
  status: string;
  proxy: string;
  last_checked: string | null;
}
export interface Task {
  id: number;
  name: string;
  account_id: number;
  kind: "signer" | "automation" | "monitor";
  enabled: boolean;
  cron: string;
  timezone: string;
  delay_min: number;
  delay_max: number;
  success_pattern: string;
  failure_pattern: string;
  response_timeout: number;
  folder: string;
  group_name: string;
  config: any;
  next_run: string | null;
  last_run: string | null;
  last_status: string;
  last_summary: string;
  running: boolean;
}
export interface Run {
  id: number;
  task_id: number | null;
  account_id: number | null;
  task_name: string;
  account_name: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  summary: string;
  response: string;
}
