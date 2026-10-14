import { expect, test } from "@playwright/test";

const defaults = {
  enabled: false,
  max_attempts: 3,
  base_delay: 60,
  max_delay: 3600,
  horizon_hours: 24,
};

test("uncertain delivery needs review before any resend on mobile", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const now = Math.floor(Date.now() / 1000);
  const row = {
    destination: "@target",
    fingerprint: "a".repeat(64),
    action: "text",
    source_chat: "-100",
    source_message: 7,
    status: "uncertain",
    attempts: 1,
    created_at: now,
    updated_at: now,
    last_error: "TimeoutError",
    account_id: 1,
    task_id: 2,
    rule_id: "r",
    dedup: "message",
    retryable: false,
    needs_review: true,
    failure_kind: "unknown",
    auto_attempts: 0,
    retry_status: "blocked",
    next_retry_at: null,
  };
  let sends = 0;
  let reviewed: unknown;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/panel/me") return route.fulfill({ json: {} });
    if (path === "/api/accounts")
      return route.fulfill({ json: [{ id: 1, name: "测试账号" }] });
    if (path === "/api/tasks")
      return route.fulfill({
        json: [{ id: 2, name: "测试转发", kind: "automation", account_id: 1 }],
      });
    if (path === "/api/overview")
      return route.fulfill({ json: { trend: [], recent: [] } });
    if (path === "/api/relay/history")
      return route.fulfill({
        json: { items: [row], total: 1, page: 1, page_size: 20 },
      });
    if (path === "/api/relay/stats")
      return route.fulfill({
        json: {
          total: 1,
          sent: 0,
          failed: 0,
          sending: 0,
          uncertain: 1,
          days: [],
        },
      });
    if (path === "/api/relay/resolve") {
      reviewed = route.request().postDataJSON();
      Object.assign(row, {
        status: "failed",
        failure_kind: "reviewed",
        retryable: true,
        needs_review: false,
      });
      return route.fulfill({ json: { entry: row } });
    }
    if (path === "/api/relay/retry") {
      sends++;
      Object.assign(row, { status: "sent", retryable: false });
      return route.fulfill({ json: { status: "sent", entry: row } });
    }
    throw new Error(path);
  });
  await page.goto("/");
  await page.getByRole("button", { name: "打开导航" }).click();
  await page.locator("nav").getByRole("button", { name: "转发记录" }).click();
  await page.getByLabel("发送状态").selectOption("uncertain");
  await page.getByRole("button", { name: "核查", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "核查投递结果" });
  expect(await dialog.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(
    true,
  );
  await dialog.screenshot({
    path: test.info().outputPath("review-dialog.png"),
  });
  await expect(
    dialog.getByRole("button", { name: "确认未送达" }),
  ).toBeDisabled();
  await dialog.getByLabel("我已核对目标聊天中的实际消息").check();
  await dialog.getByRole("button", { name: "确认未送达" }).click();
  await expect(dialog).not.toBeVisible();
  expect(sends).toBe(0);
  expect(reviewed).toMatchObject({
    attempt: 1,
    decision: "not_sent",
    confirmation: "checked_destination",
  });
  await page.getByLabel("发送状态").selectOption("");
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "重试", exact: true }).click();
  await expect.poll(() => sends).toBe(1);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("retry policy defaults off and persists explicit bounds", async ({
  page,
}) => {
  let policy = { ...defaults };
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/panel/me") return route.fulfill({ json: {} });
    if (["/api/accounts", "/api/tasks"].includes(path))
      return route.fulfill({ json: [] });
    if (path === "/api/overview")
      return route.fulfill({ json: { trend: [], recent: [] } });
    if (path === "/api/settings")
      return route.fulfill({ json: { notifications: [] } });
    if (path === "/api/maintenance")
      return route.fulfill({
        json: {
          policy: { enabled: false },
          alerts: { last_status: "idle" },
          batch_size: 500,
        },
      });
    if (path === "/api/relay/retry-policy") {
      if (route.request().method() === "PUT")
        policy = route.request().postDataJSON();
      return route.fulfill({ json: { policy, batch_size: 5, interval: 30 } });
    }
    throw new Error(path);
  });
  await page.goto("/");
  await page.locator("nav").getByRole("button", { name: "系统设置" }).click();
  await expect(page.getByRole("heading", { name: "转发可靠性" })).toBeVisible();
  await expect(page.getByLabel("启用自动重试")).not.toBeChecked();
  await page.getByLabel("启用自动重试").check();
  await page.getByLabel("自动处理次数上限").fill("2");
  await page.getByRole("button", { name: "保存重试策略" }).click();
  await expect(page.getByText("重试策略已保存")).toBeVisible();
  expect(policy.enabled).toBe(true);
  expect(policy.max_attempts).toBe(2);
});
