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

test("durable recommendation excludes overhaul and restores its protected result after reload", async ({ page, request }) => {
  const query = "想玩帝国，增加火炮和兵种，不要大修";
  await page.goto("/recommend");
  await page.getByLabel("你的战役构想").fill(query);
  const responsePromise = page.waitForResponse((response) =>
    response.url().endsWith("/api/v1/recommendation-jobs") && response.request().method() === "POST"
  );
  await page.getByRole("button", { name: "寻找适合的模组" }).click();
  const response = await responsePromise;
  expect(response.status()).toBe(202);
  const created = await response.json();
  const token = response.request().headers()["x-recommendation-token"];
  expect(token).toMatch(/^[A-Za-z0-9_-]{32,128}$/);
  expect(response.url()).not.toContain(token);
  const result = page.getByRole("region", { name: "推荐结果" });
  await expect(result.getByRole("link", { name: empireTitle, exact: true })).toBeVisible();
  const savedJob = await request.get(`/api/v1/recommendation-jobs/${created.id}`, {
    headers: { "X-Recommendation-Token": token },
  });
  expect(savedJob.ok()).toBeTruthy();
  const job = await savedJob.json();
  expect(job.status).toBe("completed");
  const run = job.result;
  expect(run.engine).toBe("rules");
  expect(run.status).toBe("completed");
  expect(run.retrieval).toMatchObject({ method: "bm25", requested_method: "bm25" });
  expect(run.items.map((item: { mod: { id: string } }) => item.mod.id)).toEqual([empireId]);
  expect(run.items[0].retrieval.channels).toEqual(["lexical"]);
  expect(run.items[0].retrieval.matched_terms.length).toBeGreaterThan(0);
  await expect(result.getByRole("link", { name: overhaulTitle, exact: true })).toHaveCount(0);
  expect((await request.get(`/api/v1/runs/${run.run_id}`)).status()).toBe(404);
  await page.reload();
  await expect(page.getByLabel("你的战役构想")).toHaveValue(query);
  await expect(result.getByRole("link", { name: empireTitle, exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "本机推荐记录" })
    .getByRole("button", { name: `查看推荐记录：${query}`, exact: true })).toBeVisible();
});

test("official game scenes load, switch with source credits, and lead to faction results", async ({ page }) => {
  await page.goto("/");
  const scene = page.locator("img.hero-painting");
  await expect(scene).toHaveAttribute("alt", /官方主视觉/);
  await expect.poll(() => scene.evaluate((image: HTMLImageElement) =>
    image.complete && image.naturalWidth > 0
  )).toBeTruthy();
  const source = page.getByRole("link", { name: "官方游戏画面 · 查看来源" });
  await expect(source).toHaveAttribute("href", /store\.steampowered\.com\/app\/1142710\//);
  const switcher = page.getByRole("group", { name: "选择战役画面" });
  const empire = switcher.getByRole("button", { name: /帝国 · 魔法与火药/ });
  await empire.focus();
  await page.keyboard.press("Enter");
  await expect(empire).toHaveAttribute("aria-pressed", "true");
  await expect(scene).toHaveAttribute("alt", /艾斯佩斯/);
  await expect.poll(() => scene.evaluate((image: HTMLImageElement) =>
    image.complete && image.naturalWidth > 0 && image.currentSrc.includes("empire-elspeth")
  )).toBeTruthy();
  await expect(source).toHaveAttribute("href", /store\.steampowered\.com\/app\/2325832\//);
  await page.getByRole("link", { name: "探索帝国模组", exact: true }).click();
  await expect(page).toHaveURL(/\/explore\?/);
  await expect(page.getByRole("textbox", { name: "搜索模组" })).toHaveValue("帝国");
  await expect(page.getByRole("link", { name: empireTitle, exact: true })).toBeVisible();
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
  const status = await request.get("/api/v1/sync/status");
  expect(status.ok()).toBeTruthy();
  expect(await status.json()).toMatchObject({
    latest_job: null,
    last_successful_refresh_at: null,
    schedule: { enabled: false, timezone: "Asia/Shanghai" },
    worker: { status: "unknown", last_seen_at: null },
  });
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
  await expect.poll(() => page.locator("img.hero-painting").evaluate(
    (image: HTMLImageElement) => image.complete && image.naturalWidth > 0
  )).toBeTruthy();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  expect(overflow).toBeFalsy();
  await page.getByRole("link", { name: "探索模组", exact: true }).first().click();
  await expect(page).toHaveURL(/\/explore/);
  await expect(page.getByRole("textbox", { name: "搜索模组" })).toBeVisible();
});

test("mobile catalogue, detail, collection and recommendation remain usable", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const noOverflow = async () => {
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBeTruthy();
  };
  await page.goto("/explore");
  await page.getByRole("textbox", { name: "搜索模组" }).fill("Empire");
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await page.getByRole("link", { name: empireTitle, exact: true }).click();
  await expect(page.getByRole("heading", { name: empireTitle, exact: true })).toBeVisible();
  await noOverflow();
  await page.getByRole("button", { name: `加入清单：${empireTitle}`, exact: true }).click();
  await page.getByRole("link", { name: /^我的清单/ }).click();
  await page.getByRole("button", { name: "检查已知前置", exact: true }).click();
  await expect(page.getByRole("link", { name: "#900000000099", exact: true })).toBeVisible();
  await noOverflow();
  await page.getByRole("link", { name: "需求推荐", exact: true }).click();
  await page.getByLabel("你的战役构想").fill("帝国增加兵种和火炮，不要大修");
  await page.getByRole("button", { name: "寻找适合的模组" }).click();
  await expect(page.getByRole("region", { name: "推荐结果" }).getByRole("link", { name: empireTitle, exact: true })).toBeVisible();
  await noOverflow();
});
