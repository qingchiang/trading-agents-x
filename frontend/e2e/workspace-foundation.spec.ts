import { expect, test } from "@playwright/test";
import { workspaceFixture } from "./fixtures/workspace";

test.beforeEach(async ({ page }) => {
  const respond = workspaceFixture();
  await page.addInitScript(() => localStorage.setItem("tradingagents-locale", "en"));
  await page.route("**/api/v1/**", route => route.fulfill({ json: respond(new URL(route.request().url()), route.request().method()) }));
});

test("keeps mobile controls sized, named and outside the closed navigation", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/timelines");
  await expect(page.getByRole("checkbox", { name: "Needs full research" })).toBeVisible();
  const box = await page.getByRole("checkbox", { name: "Needs full research" }).boundingBox();
  expect(box!.width).toBeLessThanOrEqual(24); expect(box!.height).toBeLessThanOrEqual(24);
  const menu = page.getByRole("button", { name: "Open navigation", exact: true });
  await menu.focus(); await page.keyboard.press("Tab");
  await expect(page.getByRole("searchbox")).toBeFocused();
  await menu.click(); await expect(page.getByRole("dialog")).toBeVisible();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => document.body.style.overflow)).not.toBe("hidden");
  await page.getByRole("button", { name: "Collapse sidebar" }).click();
  await expect(page.getByRole("link", { name: "New research", exact: true })).toBeVisible();
  await page.goto("/runs");
  const active = page.getByRole("tab", { name: "Active runs" });
  const trash = page.getByRole("tab", { name: "Trash", exact: true });
  expect(await active.evaluate(e => getComputedStyle(e).backgroundColor)).not.toBe(await trash.evaluate(e => getComputedStyle(e).backgroundColor));
});

test("preserves library position and keeps the selected history node within its rail", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/timelines?q=nvidia&offset=25&expanded=NVDA");
  await expect(page.locator('.library-cycles .history-node').first()).toBeVisible();
  await page.evaluate(() => scrollTo(0, 400));
  await page.locator('.library-cycles .history-select').nth(12).click();
  await expect(page.locator('.decision-hero,.markdown').first()).toBeVisible();
  await page.locator('.research-header .back-link').click();
  await expect(page).toHaveURL(/q=nvidia&offset=25&expanded=NVDA/);
  await expect(page.locator('.library-cycles .history-node').first()).toBeAttached();
  await expect.poll(() => page.evaluate(() => scrollY)).toBeGreaterThan(200);
  await page.goto('/timelines/NVDA?node=increment');
  const selected = page.locator('.history-select[aria-current]');
  await expect(selected).toBeAttached();
  await expect.poll(async () => {
    const row = await selected.boundingBox(); const rail = await page.locator('.workspace-auxiliary').boundingBox();
    return !!row && !!rail && row.y >= rail.y && row.y + row.height <= rail.y + rail.height;
  }).toBe(true);
  expect(await page.evaluate(() => scrollY)).toBe(0);
});

test("expands a truncated short thesis and shows report limitations before its narrative", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  const summary = page.locator('.research-summary').first();
  const paragraph = summary.locator('.thesis-excerpt p');
  await expect(paragraph).toBeVisible();
  await summary.getByRole('button', { name: 'Read more' }).click();
  expect(await paragraph.evaluate(e => e.scrollHeight <= e.clientHeight + 1)).toBe(true);
  await page.goto('/timelines/NVDA?node=full&view=reports&report=market');
  const warning = page.locator('#run-view-reports .research-limitations');
  await expect(warning).toBeVisible();
  const prose = page.locator('#run-view-reports .markdown').first();
  expect((await warning.boundingBox())!.y).toBeLessThan((await prose.boundingBox())!.y);
  await page.getByRole('combobox', { name: 'Research run views' }).selectOption('evidence');
  await page.getByRole('button', { name: /Return to reports/ }).click();
  await expect(page).toHaveURL(/view=reports&report=market/);
});
