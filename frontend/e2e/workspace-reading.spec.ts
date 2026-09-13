import { expect, test } from "@playwright/test";
import { workspaceFixture } from "./fixtures/workspace";

test("reads, compares, checks a source and returns to the original report", async ({ page }) => {
  const respond = workspaceFixture();
  await page.addInitScript(() => localStorage.setItem("tradingagents-locale", "en"));
  await page.route("**/api/v1/**", route => route.fulfill({ json: respond(new URL(route.request().url()), route.request().method(), route.request().postData()) }));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/timelines/NVDA?node=full&view=reports&report=market&compare_mode=1&compare=active:full&compare=active:increment");
  await expect(page.locator("#run-view-reports .markdown")).toBeVisible();
  await page.getByRole("button", { name: "Compare selected nodes" }).click();
  const comparison = page.getByRole("region", { name: "Node Comparison" });
  await expect(comparison).toBeVisible();
  await expect(page).toHaveURL(/view=compare/);
  await page.screenshot({ path: "../.scratch/research-workspace-v3/phase3-screenshots/390-comparison.png" });
  const reference = comparison.getByRole("button", { name: /Open evidence/ }).first();
  await reference.click();
  await expect(page.getByRole("dialog", { name: "Source details" })).toContainText("Order visibility");
  await page.keyboard.press("Escape");
  await expect(reference).toBeFocused();
  await comparison.getByRole("checkbox").uncheck();
  await expect(page).toHaveURL(/changed_only=0/);
  await comparison.getByRole("button", { name: "Swap sides" }).click();
  await expect(page).toHaveURL(/compare=active%3Aincrement&compare=active%3Afull/);
  await expect(comparison.locator(".comparison-baselines > section").first()).toContainText("2026-07-25");
  await page.reload();
  await expect(comparison).toBeVisible();
  await expect(comparison.getByRole("checkbox")).not.toBeChecked();
  await page.mouse.wheel(0, 700);
  await expect(page.getByRole("button", { name: "Research history", exact: true })).toBeInViewport();
  await comparison.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.locator("#run-view-reports .markdown")).toBeVisible();
  await expect(page).toHaveURL(/view=reports/);
});

test("builds a navigable outline for historical briefs without recorded sections", async ({ page }) => {
  const respond = workspaceFixture();
  await page.addInitScript(() => localStorage.setItem("tradingagents-locale", "en"));
  await page.route("**/api/v1/**", route => route.fulfill({ json: respond(new URL(route.request().url()), route.request().method(), route.request().postData()) }));
  await page.setViewportSize({ width: 1080, height: 1920 });
  await page.goto('/timelines/NVDA?node=increment');
  await page.getByRole('button', { name: 'Contents', exact: true }).click();
  await page.locator('.workspace-contents').getByRole('button', { name: 'Demand and capacity', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Demand and capacity', exact: true })).toBeInViewport();
  await expect(page.locator('.workspace-auxiliary')).toBeHidden();
  await expect(page).toHaveURL(/#research-section-/);
});
