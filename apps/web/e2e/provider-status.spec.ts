import { expect, test } from "@playwright/test";
import type { Health } from "../src/lib/types";

test("unconfigured real service remains useful and does not claim a model call", async ({ page, request }) => {
  const response = await request.get("/api/v1/health");
  expect(response.headers()["cache-control"]).toContain("no-store");
  expect(await response.json()).toMatchObject({
    llm_configured: false,
    providers: { llm: { configuration: "not_configured", last_call: null } },
  });
  await page.goto("/recommend");
  await expect(page.locator(".prompt-topline .mode-badge")).toHaveText("规则理解已就绪");
  const panel = page.getByLabel("参谋服务状态");
  await panel.locator("summary").click();
  await expect(panel).toContainText("尚无外部模型调用记录");
  await expect(panel).toContainText("关键词匹配");
  await expect(page.getByLabel("你的战役构想")).toBeEnabled();
});

test("UI fixture distinguishes configured, observed, failed and paused model states", async ({ page }) => {
  const health: Health = {
    status: "ok", version: "e2e-fixture", database: "ok", llm_configured: true,
    steam_discovery_configured: false, embedding_configured: true, retrieval_mode: "hybrid",
    providers: {
      llm: { configuration: "configured", calls_enabled: true, last_call: null },
      embedding: { configuration: "configured", calls_enabled: true, last_call: null },
    },
  };
  await page.route("**/api/v1/health", (route) => route.fulfill({ json: health }));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/recommend");
  const badge = page.locator(".prompt-topline .mode-badge");
  await expect(badge).toHaveText("模型已配置 · 待核验");
  const panel = page.getByLabel("参谋服务状态");
  await panel.locator("summary").click();
  await expect(panel).toContainText("已配置，尚无调用记录");
  await expect(panel).toContainText("本次实际使用的理解方式");

  health.providers!.llm.last_call = {
    status: "succeeded", at: "2026-10-22T01:01:00Z", completed_at: "2026-10-22T01:01:01Z", error_code: null,
  };
  await page.getByRole("button", { name: "刷新状态" }).click();
  await expect(badge).toHaveText("最近模型调用成功");
  await panel.locator("summary").click();
  await expect(panel).toContainText("北京时间");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();

  health.providers!.llm.last_call!.status = "failed";
  health.providers!.llm.last_call!.error_code = "rate_limited";
  await page.getByRole("button", { name: "刷新状态" }).click();
  await expect(badge).toHaveText("模型可回退至规则");
  await panel.locator("summary").click();
  await expect(panel).toContainText("最近调用未成功");
  await expect(panel).not.toContainText("rate_limited");

  health.providers!.llm.calls_enabled = false;
  await page.getByRole("button", { name: "刷新状态" }).click();
  await expect(badge).toHaveText("模型调用已暂停");
  await expect(page.getByLabel("你的战役构想")).toBeEnabled();
});
