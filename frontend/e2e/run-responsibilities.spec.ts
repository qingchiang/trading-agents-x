import { expect, test } from "@playwright/test";
import { workspaceFixture } from "./fixtures/workspace";

test.beforeEach(async ({ page }) => {
  const respond = workspaceFixture();
  await page.addInitScript(() => localStorage.setItem("tradingagents-locale", "en"));
  await page.route("**/api/v1/**", route => route.fulfill({ json: respond(new URL(route.request().url()), route.request().method(), route.request().postData()) }));
});

test("separates completed run progress from Timeline research reading", async ({ page }) => {
  await page.goto("/runs/full");
  await expect(page.getByRole("heading", { name: "Run progress" })).toBeVisible();
  await expect(page.locator(".decision-summary")).toHaveCount(0);
  await page.getByRole("link", { name: "Read research", exact: true }).click();
  await expect(page).toHaveURL(/\/timelines\/NVDA\?node=full/);
  await expect(page.locator(".decision-summary")).toBeVisible();
  await page.goBack();
  await expect(page.getByRole("heading", { name: "Run progress" })).toBeVisible();
  await page.goto("/timelines/NVDA?node=increment&view=brief#workspace-period-performance");
  await expect(page.locator(".performance-section")).toBeInViewport();
  await page.goto("/timelines/NVDA?node=full&view=reports&report=market#market-summary");
  await expect(page.locator("#run-view-reports")).toBeVisible();
  await page.goto("/runs/full?view=diagnostics");
  await expect(page).toHaveURL(/\/runs\/full\?view=diagnostics/);
  await expect(page.locator(".diagnostics-view")).toBeVisible();
});

test("keeps task status and cycle context without repeating research ratings", async ({ page }) => {
  await page.goto("/runs");
  await expect(page.locator(".task-group").first()).toBeVisible();
  await expect(page.locator(".task-row .research-rating-badge")).toHaveCount(0);
  await expect(page.locator(".task-row .status").first()).toBeVisible();
  await page.locator(".task-group").first().getByRole("button", { name: "Manage cycle" }).click();
  await expect(page.getByRole("link", { name: "Research Timeline" }).first()).toHaveAttribute("href", /node=full/);
});
