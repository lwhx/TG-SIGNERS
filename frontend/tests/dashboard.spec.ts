import { test, expect, Page } from "@playwright/test";
import { readFileSync } from "node:fs";
const password =
  process.env.DASHBOARD_PASSWORD ||
  readFileSync("../data/initial-password.txt", "utf8").trim();
async function login(page: Page) {
  const response = await page.request.post("/api/panel/login", {
    data: { password },
  });
  expect(response.ok()).toBeTruthy();
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "工作空间概览", exact: true }),
  ).toBeVisible();
}

test("panel login, session cookie, navigation, websocket and desktop layout", async ({
  page,
}) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "欢迎回来" })).toBeVisible();
  await page.getByLabel("管理密码", { exact: true }).fill(password);
  await page.getByRole("button", { name: "登录控制台", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "工作空间概览", exact: true }),
  ).toBeVisible();
  await page.screenshot({ path: "../docs/dashboard.png", fullPage: true });
  for (const name of [
    "Telegram 账号",
    "签到任务",
    "自动化与转发",
    "日志与终端",
    "工具箱",
    "系统设置",
  ]) {
    await page
      .locator("nav")
      .getByRole("button", { name, exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name, exact: true }),
    ).toBeVisible();
  }
  await page
    .locator("nav")
    .getByRole("button", { name: "日志与终端", exact: true })
    .click();
  await expect(page.getByText("LIVE", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "执行历史", exact: true }).click();
  await expect(page.getByText("当前筛选下没有执行记录。")).toBeVisible();
  expect(errors).toEqual([]);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
});

test("Telegram auth dialog follows code, retryable 2FA and success states (mock Telegram transport)", async ({
  page,
}) => {
  await login(page);
  await page.route("**/api/auth/send-code", (route) =>
    route.fulfill({
      json: {
        status: "CODE_SENT",
        flow_id: "test-flow",
        phone_code_hash: "test-hash",
        resend_after: 60,
      },
    }),
  );
  await page.route("**/api/auth/sign-in", (route) =>
    route.fulfill({ json: { status: "2FA_REQUIRED", flow_id: "test-flow" } }),
  );
  let attempt = 0;
  await page.route("**/api/auth/check-2fa", (route) => {
    attempt++;
    return route.fulfill(
      attempt === 1
        ? { status: 400, json: { detail: "二步验证密码错误" } }
        : { json: { status: "AUTHORIZED", account_id: 1 } },
    );
  });
  await page.getByRole("button", { name: "添加账号", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("手机号码", { exact: false }).fill("+8613800000000");
  await dialog.getByRole("button", { name: "发送验证码", exact: true }).click();
  await expect(dialog.getByLabel("登录验证码", { exact: true })).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: /秒后可重新发送/ }),
  ).toBeDisabled();
  await dialog.getByLabel("登录验证码", { exact: true }).fill("12345");
  await dialog.getByRole("button", { name: "验证并继续" }).click();
  await dialog
    .getByLabel("二步验证密码", { exact: true })
    .fill("test-only-password");
  await dialog.getByRole("button", { name: "验证密码", exact: true }).click();
  await expect(dialog.getByRole("alert")).toHaveText("二步验证密码错误");
  await dialog.getByRole("button", { name: "验证密码", exact: true }).click();
  await expect(dialog.getByRole("heading", { name: "连接成功" })).toBeVisible();
  await dialog.getByRole("button", { name: "完成", exact: true }).click();
  await expect(dialog).not.toBeVisible();
});

test("task action form submits complete config and editing retains fields (mock account and task persistence)", async ({
  page,
}) => {
  // The clock must work even when the browser has no native time popup.
  await page.addInitScript(() => {
    Object.defineProperty(HTMLInputElement.prototype, "showPicker", {
      value: undefined,
      configurable: true,
    });
  });
  const account = {
    id: 901,
    name: "Browser test account",
    phone: "+10000000000",
    username: "test",
    user_id: "901",
    avatar: "",
    status: "online",
    proxy: "",
    last_checked: null,
  };
  let tasks: any[] = [];
  await page.route("**/api/accounts", (route) =>
    route.fulfill({ json: [account] }),
  );
  await page.route("**/api/tasks", async (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      tasks = [
        {
          ...body,
          id: 902,
          last_status: "pending",
          running: false,
          last_run: null,
          next_run: null,
        },
      ];
      return route.fulfill({ status: 201, json: tasks[0] });
    }
    return route.fulfill({ json: tasks });
  });
  await page.route("**/api/tasks/902", async (route) => {
    expect(route.request().method()).toBe("PUT");
    tasks[0] = { ...tasks[0], ...route.request().postDataJSON() };
    return route.fulfill({ json: tasks[0] });
  });
  await login(page);
  await page.getByRole("button", { name: "新建任务", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: /新建签到任务|编辑任务/ });
  const controlHeights = await dialog
    .locator('input:not([type="checkbox"]):not([type="file"]), select')
    .evaluateAll((controls) =>
      controls.map((control) =>
        Math.round(control.getBoundingClientRect().height),
      ),
    );
  expect(
    Math.max(...controlHeights) - Math.min(...controlHeights),
  ).toBeLessThanOrEqual(1);
  const alignedRows = [
    ["动作间隔（秒）", "延时删除消息（秒）"],
    ["重复频率", "执行时间"],
    ["随机延迟上限（秒）", "成功正则"],
  ];
  for (const [leftLabel, rightLabel] of alignedRows) {
    const left =
      leftLabel === "重复频率"
        ? dialog.getByRole("combobox", { name: leftLabel, exact: true })
        : dialog.getByLabel(leftLabel, { exact: true });
    const right =
      rightLabel === "执行时间"
        ? dialog.getByRole("textbox", { name: rightLabel, exact: true })
        : dialog.getByLabel(rightLabel, { exact: false });
    const [leftBox, rightBox] = await Promise.all([
      left.boundingBox(),
      right.boundingBox(),
    ]);
    expect(leftBox).not.toBeNull();
    expect(rightBox).not.toBeNull();
    expect(Math.abs(leftBox!.y - rightBox!.y)).toBeLessThanOrEqual(1);
  }
  await dialog.getByLabel("任务名称", { exact: true }).fill("浏览器回归签到");
  await dialog.getByLabel("Bot / Chat ID", { exact: true }).fill("@TestBot");
  await dialog.getByRole("button", { name: "添加动作", exact: true }).click();
  await dialog.getByLabel("动作 2 类型", { exact: true }).selectOption("3");
  await dialog.getByLabel("动作 2 文本", { exact: true }).fill("签到");
  await dialog.getByLabel("成功正则", { exact: false }).fill("签到成功");
  await dialog.getByRole("button", { name: "选择执行时间" }).click();
  const timePicker = page.getByRole("dialog", {
    name: "选择执行时间",
    exact: true,
  });
  await expect(timePicker).toBeVisible();
  await timePicker.getByRole("listbox", { name: "小时" }).selectOption("09");
  await timePicker.getByRole("listbox", { name: "分钟" }).selectOption("45");
  await timePicker.getByRole("button", { name: "完成", exact: true }).click();
  await expect(timePicker).not.toBeVisible();
  await expect(dialog.getByLabel("执行时间", { exact: true })).toHaveValue(
    "09:45",
  );
  await dialog.getByRole("button", { name: "校验配置", exact: true }).click();
  await expect(page.getByText("配置校验通过", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "保存任务", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(tasks[0].config.chats[0].actions).toEqual([
    { action: 1, text: "/checkin" },
    { action: 3, text: "签到" },
  ]);
  expect(tasks[0].delay_min).toBe(10);
  expect(tasks[0].cron).toBe("45 9 * * *");
  expect(tasks[0].config.sign_at).toBe("45 9 * * *");
  await page
    .locator("nav")
    .getByRole("button", { name: "签到任务", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "浏览器回归签到", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("每天 09:45", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "浏览器回归签到", exact: true })
    .click();
  await expect(dialog.getByLabel("动作 2 文本", { exact: true })).toHaveValue(
    "签到",
  );
  await expect(dialog.getByLabel("执行时间", { exact: true })).toHaveValue(
    "09:45",
  );
  await dialog.getByRole("button", { name: "选择执行时间" }).click();
  await expect(timePicker.getByRole("listbox", { name: "小时" })).toHaveValue(
    "09",
  );
  await expect(timePicker.getByRole("listbox", { name: "分钟" })).toHaveValue(
    "45",
  );
  await page.keyboard.press("Escape");
  await expect(timePicker).not.toBeVisible();
  await expect(dialog).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "选择执行时间" }),
  ).toBeFocused();
  await dialog.getByLabel("执行时间", { exact: true }).fill("07:05");
  await dialog
    .getByRole("combobox", { name: "重复频率", exact: true })
    .selectOption("weekly");
  await dialog.getByLabel("周一", { exact: true }).uncheck();
  await dialog.getByRole("button", { name: "保存任务", exact: true }).click();
  await expect(
    dialog.getByText("请至少选择一个执行星期", { exact: true }),
  ).toBeVisible();
  await dialog.getByLabel("周日", { exact: true }).check();
  await dialog.getByRole("button", { name: "保存任务", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(tasks[0].cron).toBe("5 7 * * 0");
  await expect(page.getByText("周日 07:05", { exact: true })).toBeVisible();
  await page
    .getByRole("button", { name: "浏览器回归签到", exact: true })
    .click();
  await dialog
    .getByRole("combobox", { name: "重复频率", exact: true })
    .selectOption("monthly");
  const monthDates = dialog.getByRole("group", {
    name: "每月执行日期",
    exact: true,
  });
  await monthDates
    .getByRole("checkbox", { name: "1 日", exact: true })
    .uncheck();
  await dialog.getByRole("button", { name: "保存任务", exact: true }).click();
  await expect(
    dialog.getByText("请至少选择一个每月执行日期", { exact: true }),
  ).toBeVisible();
  for (const day of [31, 15, 1]) {
    await monthDates
      .getByRole("checkbox", { name: `${day} 日`, exact: true })
      .check();
  }
  await expect(
    monthDates.getByText("当月没有的日期会跳过，其余所选日期照常执行。"),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "校验配置", exact: true }).click();
  await expect(page.getByText("配置校验通过", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "保存任务", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(tasks[0].cron).toBe("5 7 1,15,31 * *");
  expect(tasks[0].config.sign_at).toBe("5 7 1,15,31 * *");
  await expect(
    page.getByText("每月 1、15、31 日 07:05", { exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "浏览器回归签到", exact: true })
    .click();
  await expect(monthDates.getByRole("checkbox", { checked: true })).toHaveCount(
    3,
  );
  for (const day of [1, 15, 31]) {
    await expect(
      monthDates.getByRole("checkbox", { name: `${day} 日`, exact: true }),
    ).toBeChecked();
  }
  for (const day of [1, 31]) {
    await monthDates
      .getByRole("checkbox", { name: `${day} 日`, exact: true })
      .uncheck();
  }
  await dialog.getByRole("button", { name: "保存任务", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(tasks[0].cron).toBe("5 7 15 * *");
  await page
    .getByRole("button", { name: "浏览器回归签到", exact: true })
    .click();
  await expect(monthDates.getByRole("checkbox", { checked: true })).toHaveCount(
    1,
  );
  await expect(
    monthDates.getByRole("checkbox", { name: "15 日", exact: true }),
  ).toBeChecked();
  await dialog.getByRole("button", { name: "关闭弹窗", exact: true }).click();
  // An imported expression outside the picker must survive unrelated edits.
  tasks[0].cron = "*/15 8-18 * * 1-5";
  tasks[0].config.sign_at = tasks[0].cron;
  await page.reload();
  await page
    .locator("nav")
    .getByRole("button", { name: "签到任务", exact: true })
    .click();
  await page
    .getByRole("button", { name: "浏览器回归签到", exact: true })
    .click();
  await expect(
    dialog.getByRole("combobox", { name: "重复频率", exact: true }),
  ).toHaveValue("custom");
  await expect(dialog.getByLabel("执行时间", { exact: true })).toHaveCount(0);
  await dialog.getByRole("button", { name: "完整 JSON", exact: true }).click();
  await expect(dialog.getByLabel("完整任务配置 JSON")).toContainText("TestBot");
  await dialog.getByRole("button", { name: "保存任务", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  expect(tasks[0].cron).toBe("*/15 8-18 * * 1-5");
  let toolRequest: any;
  await page.route("**/api/tools", async (route) => {
    toolRequest = route.request().postDataJSON();
    return route.fulfill({ json: { result: "模拟定时消息已创建" } });
  });
  await page
    .locator("nav")
    .getByRole("button", { name: "工具箱", exact: true })
    .click();
  await page
    .getByRole("combobox", { name: "操作", exact: true })
    .selectOption("schedule");
  await page
    .getByLabel("Chat ID / @username", { exact: true })
    .fill("@TestBot");
  await page.getByLabel("消息文本", { exact: true }).fill("/checkin");
  await page
    .getByRole("combobox", { name: "重复频率", exact: true })
    .selectOption("monthly");
  const toolDates = page.getByRole("group", {
    name: "每月执行日期",
    exact: true,
  });
  await toolDates.getByRole("checkbox", { name: "15 日", exact: true }).check();
  await toolDates.getByRole("checkbox", { name: "28 日", exact: true }).check();
  await page.getByLabel("执行时间", { exact: true }).fill("20:15");
  await page.getByRole("button", { name: "选择执行时间" }).click();
  await expect(timePicker.getByRole("listbox", { name: "小时" })).toHaveValue(
    "20",
  );
  await expect(timePicker.getByRole("listbox", { name: "分钟" })).toHaveValue(
    "15",
  );
  await page.getByRole("heading", { name: "工具箱", exact: true }).click();
  await expect(timePicker).not.toBeVisible();
  await page.getByRole("button", { name: "执行操作", exact: true }).click();
  await expect(
    page.getByText('"模拟定时消息已创建"', { exact: true }),
  ).toBeVisible();
  expect(toolRequest.cron).toBe("15 20 1,15,28 * *");
});

test("mobile navigation and dialog fit within viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page);
  await page.getByRole("button", { name: "打开导航" }).click();
  await page
    .locator("nav")
    .getByRole("button", { name: "Telegram 账号", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Telegram 账号", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "添加账号", exact: true })
    .first()
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBeTruthy();
  await page.screenshot({ path: "../docs/mobile.png", fullPage: true });
});

test("mobile task clock opens a picker inside the viewport and accepts boundary times", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/accounts", (route) =>
    route.fulfill({
      json: [
        {
          id: 903,
          name: "Time picker test account",
          phone: "+10000000000",
          status: "online",
        },
      ],
    }),
  );
  await login(page);
  await page.getByRole("button", { name: "新建任务", exact: true }).click();
  const dialog = page.getByRole("dialog", {
    name: "新建签到任务",
    exact: true,
  });
  const clock = dialog.getByRole("button", { name: "选择执行时间" });
  await clock.click();
  const picker = page.getByRole("dialog", {
    name: "选择执行时间",
    exact: true,
  });
  await expect(picker).toBeVisible();
  const bounds = await picker.boundingBox();
  expect(bounds).not.toBeNull();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.y).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(844);
  await picker.getByRole("listbox", { name: "小时" }).selectOption("23");
  await picker.getByRole("listbox", { name: "分钟" }).selectOption("59");
  await picker.getByRole("button", { name: "完成", exact: true }).click();
  await expect(dialog.getByLabel("执行时间", { exact: true })).toHaveValue(
    "23:59",
  );
  await clock.click();
  await picker.getByRole("listbox", { name: "小时" }).selectOption("00");
  await picker.getByRole("listbox", { name: "分钟" }).selectOption("00");
  await clock.click();
  await expect(picker).not.toBeVisible();
  await expect(dialog.getByLabel("执行时间", { exact: true })).toHaveValue(
    "00:00",
  );
  await dialog
    .getByRole("combobox", { name: "重复频率", exact: true })
    .selectOption("monthly");
  const monthDates = dialog.getByRole("group", {
    name: "每月执行日期",
    exact: true,
  });
  await monthDates
    .getByRole("checkbox", { name: "15 日", exact: true })
    .check();
  await monthDates
    .getByRole("checkbox", { name: "31 日", exact: true })
    .check();
  await expect(monthDates.getByRole("checkbox", { checked: true })).toHaveCount(
    3,
  );
  const dateBounds = await monthDates.boundingBox();
  expect(dateBounds!.x).toBeGreaterThanOrEqual(0);
  expect(dateBounds!.x + dateBounds!.width).toBeLessThanOrEqual(390);
  await dialog.getByLabel("执行时间", { exact: true }).fill("");
  await clock.click();
  await picker.getByRole("button", { name: "完成", exact: true }).click();
  await expect(dialog.getByLabel("执行时间", { exact: true })).toHaveValue(
    "00:00",
  );
});

test("live console falls back to EventSource when WebSocket is unavailable", async ({
  page,
}) => {
  await page.routeWebSocket(/\/ws\/logs$/, (socket) =>
    socket.close({ code: 1013, reason: "test fallback" }),
  );
  await login(page);
  await page
    .locator("nav")
    .getByRole("button", { name: "日志与终端", exact: true })
    .click();
  await expect(
    page.getByText("EventSource 实时日志流", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("LIVE", { exact: true })).toBeVisible();
});
