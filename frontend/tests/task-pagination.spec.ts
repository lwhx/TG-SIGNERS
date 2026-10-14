import { expect, Page, test } from "@playwright/test";
import type { Task } from "../lib/api";

async function openTasks(page: Page, count: number) {
  const state = {
    reads: 0,
    tasks: Array.from(
      { length: count },
      (_, index): Task => ({
        id: index + 1,
        name: `分页任务 ${String(index + 1).padStart(3, "0")}`,
        account_id: 1,
        kind: "signer",
        enabled: false,
        cron: "0 8 * * *",
        timezone: "Asia/Shanghai",
        delay_min: 0,
        delay_max: 0,
        success_pattern: "",
        failure_pattern: "",
        response_timeout: 30,
        folder: "",
        config: { chats: [] },
        next_run: null,
        last_run: null,
        last_status: "pending",
        last_summary: "",
        running: false,
      }),
    ),
  };
  // All API requests stay in the browser; no real tasks or sessions are used.
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/panel/me") return route.fulfill({ json: {} });
    if (path === "/api/accounts") return route.fulfill({ json: [] });
    if (path === "/api/overview")
      return route.fulfill({ json: { trend: [], recent: [] } });
    if (path === "/api/tasks" && route.request().method() === "GET") {
      state.reads++;
      return route.fulfill({ json: state.tasks });
    }
    if (
      path.startsWith("/api/tasks/") &&
      route.request().method() === "DELETE"
    ) {
      const id = Number(path.split("/").pop());
      state.tasks = state.tasks.filter((task) => task.id !== id);
      return route.fulfill({ json: { ok: true } });
    }
    throw new Error(
      `Unexpected API request: ${route.request().method()} ${path}`,
    );
  });
  await page.goto("/");
  if ((page.viewportSize()?.width || 1440) < 1024)
    await page.getByRole("button", { name: "打开导航" }).click();
  await page
    .locator("nav")
    .getByRole("button", { name: "签到任务", exact: true })
    .click();
  await expect(page.locator("tbody tr")).toHaveCount(Math.min(count, 20));
  return state;
}

test("12 tasks fit on one page with explicit totals and page size controls", async ({
  page,
}) => {
  await openTasks(page, 12);
  await expect(page.getByRole("combobox", { name: "每页条数" })).toHaveValue(
    "20",
  );
  await expect(
    page.getByRole("button", { name: "分页任务 012", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "共" })).toHaveText(
    "共 12 条 · 显示 1–12 条 · 第 1 / 1 页",
  );
  const pagination = page.getByRole("navigation", { name: "表格分页" });
  await expect(
    pagination.getByRole("button", { name: "第 1 页" }),
  ).toHaveAttribute("aria-current", "page");
  await expect(
    pagination.getByRole("button", { name: "上一页" }),
  ).toBeDisabled();
  await expect(
    pagination.getByRole("button", { name: "下一页" }),
  ).toBeDisabled();
});

test("pagination survives repeated polling, manual refresh and unrelated rerenders", async ({
  page,
}) => {
  await page.clock.install();
  const state = await openTasks(page, 45);
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  const summary = page.getByRole("status").filter({ hasText: "共" });
  await expect(summary).toHaveText("共 45 条 · 显示 21–40 条 · 第 2 / 3 页");
  for (const lastStatus of ["success", "failed"]) {
    state.tasks[20].last_status = lastStatus;
    const before = state.reads;
    await page.clock.fastForward(15000);
    await expect.poll(() => state.reads).toBeGreaterThan(before);
    await expect(page.locator("tbody tr").first()).toContainText(
      lastStatus === "success" ? "成功" : "失败",
    );
    await expect(summary).toHaveText("共 45 条 · 显示 21–40 条 · 第 2 / 3 页");
  }
  await page.getByRole("button", { name: "切换暗色主题" }).click();
  const before = state.reads;
  await page.getByRole("button", { name: "刷新页面数据" }).click();
  await expect.poll(() => state.reads).toBeGreaterThan(before);
  await expect(summary).toHaveText("共 45 条 · 显示 21–40 条 · 第 2 / 3 页");
  await expect(page.locator("tbody tr").first()).toContainText("分页任务 021");
});

test("numbered pages, page sizes, searching, sorting and task category changes stay valid", async ({
  page,
}) => {
  await openTasks(page, 245);
  const summary = page.getByRole("status").filter({ hasText: "共" });
  const pagination = page.getByRole("navigation", { name: "表格分页" });
  await pagination
    .getByRole("button", { name: "第 13 页", exact: true })
    .click();
  await expect(summary).toHaveText(
    "共 245 条 · 显示 241–245 条 · 第 13 / 13 页",
  );
  await expect(
    pagination.getByRole("button", { name: "下一页" }),
  ).toBeDisabled();
  await pagination.getByRole("button", { name: "上一页" }).click();
  await expect(summary).toContainText("第 12 / 13 页");
  const size = page.getByRole("combobox", { name: "每页条数" });
  await size.selectOption("50");
  await expect(page.locator("tbody tr")).toHaveCount(50);
  await expect(summary).toContainText("第 1 / 5 页");
  await size.selectOption("100");
  await expect(page.locator("tbody tr")).toHaveCount(100);
  await pagination
    .getByRole("button", { name: "第 2 页", exact: true })
    .click();
  const search = page.getByRole("textbox", { name: "搜索任务" });
  await search.fill("245");
  await expect(summary).toHaveText("共 1 条 · 显示 1–1 条 · 第 1 / 1 页");
  await expect(size).toHaveValue("100");
  await search.fill("没有匹配结果");
  await expect(summary).toHaveText("共 0 条 · 显示 0–0 条 · 第 1 / 1 页");
  await search.fill("");
  await pagination
    .getByRole("button", { name: "第 2 页", exact: true })
    .click();
  await page
    .getByRole("button", { name: "任务名称 / 目标", exact: true })
    .click();
  await expect(summary).toContainText("第 1 / 3 页");
  await pagination
    .getByRole("button", { name: "第 2 页", exact: true })
    .click();
  await page
    .locator("nav")
    .getByRole("button", { name: "自动化与转发", exact: true })
    .click();
  await expect(summary).toHaveText("共 0 条 · 显示 0–0 条 · 第 1 / 1 页");
  await page
    .locator("nav")
    .getByRole("button", { name: "签到任务", exact: true })
    .click();
  await expect(summary).toContainText("第 1 / 13 页");
});

test("search matches task names, Bot usernames, saved target names and numeric Chat IDs across pages", async ({
  page,
}) => {
  const state = await openTasks(page, 45);
  state.tasks[40].config.chats = [
    { chat_id: "@DailyCheckBot", name: "每日签到助手" },
    { chat_id: -1001234567890 },
  ];
  state.tasks[41].config.chats = [{ chat_id: "987654321" }];
  await page.getByRole("button", { name: "刷新页面数据" }).click();
  await page.getByRole("button", { name: "第 2 页", exact: true }).click();
  const search = page.getByRole("textbox", { name: "搜索任务" });
  for (const term of [
    "@dailycheckbot",
    "  DAILYCHECK  ",
    "每日签到",
    "-1001234567890",
    "1234567890",
    "分页任务 041",
  ]) {
    await search.fill(term);
    await expect(page.locator("tbody tr")).toHaveCount(1);
    await expect(
      page.getByRole("button", { name: "分页任务 041", exact: true }),
    ).toBeVisible();
    await expect(page.getByRole("status").filter({ hasText: "共" })).toHaveText(
      "共 1 条 · 显示 1–1 条 · 第 1 / 1 页",
    );
  }
  await search.fill("987654321");
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await expect(
    page.getByRole("button", { name: "分页任务 042", exact: true }),
  ).toBeVisible();
  await search.fill("   ");
  await expect(page.getByRole("status").filter({ hasText: "共" })).toHaveText(
    "共 45 条 · 显示 1–20 条 · 第 1 / 3 页",
  );
});

test("deleting the only task on the last page returns to the nearest remaining page", async ({
  page,
}) => {
  const state = await openTasks(page, 41);
  await page.getByRole("button", { name: "第 3 页", exact: true }).click();
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await page.getByRole("button", { name: "更多任务操作" }).click();
  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "删除任务", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "共" })).toHaveText(
    "共 40 条 · 显示 21–40 条 · 第 2 / 2 页",
  );
  await expect(page.locator("tbody tr")).toHaveCount(20);
  await expect(page.locator("tbody tr").first()).toContainText("分页任务 021");
  await page.keyboard.press("Escape");
  state.tasks = [];
  await page.getByRole("button", { name: "刷新页面数据" }).click();
  await expect(page.getByRole("status").filter({ hasText: "共" })).toHaveText(
    "共 0 条 · 显示 0–0 条 · 第 1 / 1 页",
  );
  await expect(
    page.getByRole("button", { name: "上一页", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "下一页", exact: true }),
  ).toBeDisabled();
});

test("pagination remains usable inside a mobile viewport with many pages", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openTasks(page, 245);
  const pagination = page.getByRole("navigation", { name: "表格分页" });
  await pagination
    .getByRole("button", { name: "第 13 页", exact: true })
    .click();
  await expect(
    page.getByRole("status").filter({ hasText: "共" }),
  ).toContainText("第 13 / 13 页");
  for (const control of await pagination.getByRole("button").all()) {
    const bounds = await control.boundingBox();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
  }
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});
