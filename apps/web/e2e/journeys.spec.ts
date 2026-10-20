import { expect, test } from "@playwright/test";

const empireId = "900000000001";
const empireTitle = "E2E Empire Artillery｜帝国火炮扩展";
const overhaulTitle = "E2E Campaign Overhaul｜战役大修";

test("catalogue searches real API, preserves unknown facts, and links to the source", async ({ page }) => {
  await page.goto("/explore");
  await expect(page.getByRole("link", { name: empireTitle, exact: true })).toBeVisible();
  await page.getByRole("textbox", { name: "搜索模组" }).fill("Empire");
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await expect(page).toHaveURL(/q=Empire/);
  await expect(page.getByRole("link", { name: empireTitle, exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: overhaulTitle, exact: true })).toHaveCount(0);
  await page.getByRole("link", { name: empireTitle, exact: true }).click();
  await expect(page.getByRole("heading", { name: empireTitle, exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "在 Steam Workshop 查看", exact: true })).toHaveAttribute(
    "href", `https://steamcommunity.com/sharedfiles/filedetails/?id=${empireId}`
  );
  await expect(page.locator(".fact-list > div").filter({ hasText: "游戏内中文" })).toContainText("尚未确认");
  await expect(page.locator(".fact-list > div").filter({ hasText: "版本兼容性" })).toContainText("尚未确认");
});

test("bounded recommendation excludes overhaul and its run is persisted", async ({ page, request }) => {
  await page.goto("/recommend");
  await page.getByLabel("你的战役构想").fill("想玩帝国，增加火炮和兵种，不要大修");
  const responsePromise = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/recommendations") && response.request().method() === "POST"
  );
  await page.getByRole("button", { name: "寻找适合的模组" }).click();
  const response = await responsePromise;
  expect(response.ok()).toBeTruthy();
  const run = await response.json();
  expect(run.engine).toBe("rules");
  expect(run.status).toBe("completed");
  expect(run.items.map((item: { mod: { id: string } }) => item.mod.id)).toEqual([empireId]);
  const result = page.getByRole("region", { name: "推荐结果" });
  await expect(result.getByRole("link", { name: empireTitle, exact: true })).toBeVisible();
  await expect(result.getByRole("link", { name: overhaulTitle, exact: true })).toHaveCount(0);
  const savedRun = await request.get(`/api/v1/runs/${run.run_id}`);
  expect(savedRun.ok()).toBeTruthy();
  expect((await savedRun.json()).run_id).toBe(run.run_id);
});

test("saved collection survives reload and reports a missing known dependency", async ({ page }) => {
  await page.goto(`/mods/${empireId}`);
  await page.getByRole("button", { name: `加入清单：${empireTitle}`, exact: true }).click();
  await page.getByRole("link", { name: /^我的清单/ }).click();
  await expect(page.getByRole("link", { name: empireTitle, exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("link", { name: empireTitle, exact: true })).toBeVisible();
  await page.getByRole("button", { name: "检查已知前置", exact: true }).click();
  await expect(page.getByRole("link", { name: "#900000000099", exact: true })).toBeVisible();
  await expect(page.getByText(/组合兼容性尚未确认/)).toBeVisible();
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出链接", exact: true }).click();
  expect((await downloadPromise).suggestedFilename()).toBe("modsage-modlist.txt");
});

test("empty and unavailable catalogue states remain explicit", async ({ page }) => {
  await page.goto("/explore?q=THIS_FIXTURE_DOES_NOT_EXIST");
  await expect(page.getByRole("heading", { name: "暂时没有匹配的收录内容" })).toBeVisible();
  await page.route("**/api/v1/mods?*", (route) => route.fulfill({
    status: 503,
    contentType: "application/json",
    body: JSON.stringify({ message: "E2E 数据服务暂时不可用" }),
  }));
  await page.goto("/explore");
  await expect(page.getByRole("alert").filter({ hasText: "暂时无法读取数据" })).toContainText("E2E 数据服务暂时不可用");
  await expect(page.getByRole("link", { name: empireTitle, exact: true })).toHaveCount(0);
});

test("public proxy blocks admin access, invalid bodies, and oversized requests", async ({ request }) => {
  expect((await request.get("/api/v1/admin/sync")).status()).toBe(404);
  expect((await request.post("/api/v1/admin/sync", { data: {} })).status()).toBe(404);
  expect((await request.post("/api/v1/recommendations", {
    headers: { "Content-Type": "application/json" }, data: Buffer.from("{invalid"),
  })).status()).toBe(400);
  expect((await request.post("/api/v1/recommendations", {
    data: { query: "x".repeat(17_000) },
  })).status()).toBe(413);
});

test("mobile homepage and navigation fit the viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByRole("link", { name: "ModSage 首页" })).toBeVisible();
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  expect(overflow).toBeFalsy();
  await page.getByRole("link", { name: "探索模组", exact: true }).first().click();
  await expect(page).toHaveURL(/\/explore/);
  await expect(page.getByRole("textbox", { name: "搜索模组" })).toBeVisible();
});
