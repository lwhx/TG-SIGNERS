import { expect, test } from "@playwright/test";

test("maintenance defaults off, previews and explicitly cleans, with mobile layout", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  let policy = {
    enabled: false,
    run_days: 90,
    relay_sent_days: 90,
    relay_failed_days: 180,
    alert_enabled: true,
    alert_window_minutes: 5,
    alert_cooldown_minutes: 30,
  };
  let cleaned = 0;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/panel/me") return route.fulfill({ json: {} });
    if (["/api/accounts", "/api/tasks"].includes(path))
      return route.fulfill({ json: [] });
    if (path === "/api/overview")
      return route.fulfill({ json: { trend: [], recent: [] } });
    if (path === "/api/relay/retry-policy")
      return route.fulfill({
        json: {
          policy: {
            enabled: false,
            max_attempts: 3,
            base_delay: 60,
            max_delay: 3600,
            horizon_hours: 24,
          },
        },
      });
    if (path === "/api/settings")
      return route.fulfill({ json: { notifications: [] } });
    if (path === "/api/maintenance/policy") {
      policy = route.request().postDataJSON();
      return route.fulfill({
        json: { policy, batch_size: 500, alerts: { last_status: "idle" } },
      });
    }
    if (path === "/api/maintenance/preview")
      return route.fulfill({ json: { runs: 7, relay: 11, batch_size: 500 } });
    if (path === "/api/maintenance/cleanup") {
      cleaned++;
      return route.fulfill({ json: { runs: 7, relay: 11, batch_size: 500 } });
    }
    if (path === "/api/maintenance")
      return route.fulfill({
        json: {
          policy,
          batch_size: 500,
          last_cleanup: null,
          alerts: { last_status: "idle" },
        },
      });
    throw new Error(path);
  });
  await page.goto("/");
  // The mobile drawer uses an accessible menu button.
  await page.getByRole("button", { name: "打开导航" }).click();
  await page.locator("nav").getByRole("button", { name: "系统设置" }).click();
  await expect(
    page.getByRole("heading", { name: "运行与历史保留" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "清理一批历史" }),
  ).toBeDisabled();
  await page.getByRole("button", { name: "预览可清理记录" }).click();
  await expect(
    page.getByText("可清理：执行历史 7 条，转发历史 11 条"),
  ).toBeVisible();
  await page.getByLabel("启用历史清理").check();
  await page.getByRole("button", { name: "保存维护策略" }).click();
  await expect(page.getByText("维护策略已保存")).toBeVisible();
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "清理一批历史" }).click();
  await expect.poll(() => cleaned).toBe(1);
  expect(policy.enabled).toBe(true);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("polling pauses while hidden and does not fetch overview on other pages", async ({
  page,
}) => {
  await page.clock.install();
  const counts: Record<string, number> = {};
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    counts[path] = (counts[path] || 0) + 1;
    const json =
      path === "/api/accounts" || path === "/api/tasks"
        ? []
        : path === "/api/overview"
          ? { trend: [], recent: [] }
          : {};
    return route.fulfill({ json });
  });
  await page.goto("/");
  await expect(page.getByText("最近同步：", { exact: false })).toBeVisible();
  await page
    .locator("nav")
    .getByRole("button", { name: "Telegram 账号" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Telegram 账号", exact: true }),
  ).toBeVisible();
  const overviewCount = counts["/api/overview"];
  await page.clock.fastForward(16000);
  expect(counts["/api/overview"]).toBe(overviewCount);
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "hidden",
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  const before = counts["/api/accounts"];
  await page.clock.fastForward(60000);
  expect(counts["/api/accounts"]).toBe(before);
  await page.evaluate(() => {
    Object.defineProperty(document, "visibilityState", {
      configurable: true,
      value: "visible",
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await expect.poll(() => counts["/api/accounts"]).toBeGreaterThan(before);
});
