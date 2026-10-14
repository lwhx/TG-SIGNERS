import { expect, test } from "@playwright/test";

test("relay dashboard shows records, filters statuses and safely confirms retries", async ({ page }) => {
  const now = Math.floor(Date.now() / 1000);
  const ref = "a".repeat(64);
  const rows = [
    {
      destination: "@archive",
      fingerprint: ref,
      action: "text",
      source_chat: "-10012345",
      source_message: 23,
      status: "failed",
      attempts: 1,
      created_at: now - 120,
      updated_at: now - 60,
      last_error: "FloodWait",
      account_id: 1,
      task_id: 5,
      rule_id: "daily",
      dedup: "message",
      retryable: true,
    },
    {
      destination: "@legacy",
      fingerprint: "b".repeat(64),
      action: "forward",
      source_chat: "-100678",
      source_message: 9,
      status: "failed",
      attempts: 1,
      created_at: now - 500,
      updated_at: now - 200,
      last_error: "RPCError",
      account_id: null,
      task_id: null,
      rule_id: null,
      dedup: null,
      retryable: false,
    },
    {
      destination: "@success",
      fingerprint: "c".repeat(64),
      action: "copy",
      source_chat: "-10012345",
      source_message: 24,
      status: "sent",
      attempts: 1,
      created_at: now - 100,
      updated_at: now - 30,
      last_error: null,
      account_id: 1,
      task_id: 5,
      rule_id: "daily",
      dedup: "message",
      retryable: false,
    },
  ];
  const apiCalls: { destination: string; fingerprint: string }[] = [];

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === "/api/panel/me") return route.fulfill({ json: { user: "admin" } });
    if (path === "/api/accounts") return route.fulfill({
      json: [{ id: 1, name: "测试账号", status: "online", phone: "",
        username: "demo", user_id: "1", avatar: "", proxy: "", last_checked: null }],
    });
    if (path === "/api/tasks") return route.fulfill({ json: [
      { id: 5, name: "每日转发", kind: "automation", account_id: 1,
        enabled: true, running: true, config: { rules: [] } },
    ] });
    if (path === "/api/overview") return route.fulfill({ json: {
      trend: [], recent: [], accounts: 1, tasks: 1, running: 1,
    } });
    if (path === "/api/relay/history") {
      const status = url.searchParams.get("status");
      const accountId = url.searchParams.get("account_id");
      const taskId = url.searchParams.get("task_id");
      const pageIndex = Number(url.searchParams.get("page") || "1");
      const filtered = rows.filter((item) =>
        (!status || item.status === status) &&
        (!accountId || item.account_id === Number(accountId)) &&
        (!taskId || item.task_id === Number(taskId))
      );
      return route.fulfill({ json: {
        items: filtered.slice((pageIndex - 1) * 20, pageIndex * 20),
        total: filtered.length, page: pageIndex, page_size: 20,
      } });
    }
    if (path === "/api/relay/stats") return route.fulfill({ json: {
      total: rows.length,
      sent: rows.filter((item) => item.status === "sent").length,
      failed: rows.filter((item) => item.status === "failed").length,
      sending: 0,
      last_24h: rows.length,
      success_rate: 33.3,
      days: [{ date: "2026-10-14", sent: 1, failed: 2, sending: 0 }],
    } });
    if (path === "/api/relay/retry" && route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      apiCalls.push(body);
      const item = rows.find((row) => row.fingerprint === body.fingerprint)!;
      item.status = "sent";
      item.retryable = false;
      item.attempts = 2;
      item.last_error = null;
      return route.fulfill({ json: { status: "sent", entry: item } });
    }
    throw new Error("Unexpected API request: " + route.request().method() + " " + path);
  });

  await page.goto("/");
  await page.locator("nav").getByRole("button", { name: "转发记录" }).click();
  await expect(page.getByRole("heading", { name: "转发历史" })).toBeVisible();
  await expect(page.getByText("最近 7 天投递结果")).toBeVisible();
  await expect(page.getByText("每日转发")).toBeVisible();
  await expect(page.getByText("FloodWait")).toBeVisible();
  await expect(page.locator("tbody tr")).toHaveCount(3);

  await page.getByLabel("发送状态").selectOption("failed");
  await expect(page.locator("tbody tr")).toHaveCount(2);
  await expect(page.getByText("无法安全重试")).toBeVisible();

  page.on("dialog", async (dialog) => dialog.accept());
  await page.getByRole("button", { name: "重试", exact: true }).click();
  await expect.poll(() => apiCalls.length).toBe(1);
  expect(apiCalls[0]).toEqual({ destination: "@archive", fingerprint: ref });
  await expect(page.getByText("重试发送成功")).toBeVisible();
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await expect(page.getByText("@legacy")).toBeVisible();
  await expect(page.getByRole("button", { name: "重试", exact: true })).toHaveCount(0);
});
