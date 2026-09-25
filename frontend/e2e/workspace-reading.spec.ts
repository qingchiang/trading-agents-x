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
  await expect(page).toHaveURL(/#legacy-/);
});


test("returns from baseline evidence to the exact update and reads performance without disclosure", async ({ page }) => {
  const respond = workspaceFixture();
  await page.addInitScript(() => localStorage.setItem("tradingagents-locale", "en"));
  await page.route("**/api/v1/**", route => route.fulfill({ json: respond(new URL(route.request().url()), route.request().method(), route.request().postData()) }));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/timelines/NVDA?node=increment&view=evidence");
  await page.getByRole("link", { name: "View full baseline evidence" }).click();
  await expect(page).toHaveURL(/node=full&view=evidence/);
  await page.getByRole("link", { name: "Return to update evidence" }).click();
  await expect(page).toHaveURL(/node=increment&view=evidence/);
  await page.goto("/timelines/NVDA?node=increment&view=brief");
  await expect(page.locator(".performance-endpoints").first()).toContainText("100 → 112");
  await expect(page.locator(".performance-retrieved").first()).toContainText("UTC");
  await expect(page.locator(".performance-section details")).toHaveCount(0);
});

test("keeps long tokens, paragraphs and table endpoints readable and restores stable section links", async ({ page }) => {
  const respond = workspaceFixture();
  const token = "LONGTOKEN".repeat(150) + "ENDTOKEN";
  await page.addInitScript(() => localStorage.setItem("tradingagents-locale", "en"));
  await page.route("**/api/v1/**", route => {
    const url = new URL(route.request().url());
    const value = respond(url, route.request().method(), route.request().postData()) as Record<string, any>;
    if (url.pathname === "/api/v1/runs/full") {
      value.result.decision.executive_summary = `Opening paragraph.\n\n${token}\n\nLast paragraph.`;
      value.result.decision.thesis = "| First column | Last column |\n| --- | --- |\n| " + "WIDE ".repeat(60) + " | Endpoint |";
    }
    return route.fulfill({ json: value });
  });
  for (const width of [390, 1080, 2560]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/timelines/NVDA?node=full#assessment-thesis");
    const thesis = page.locator("#assessment-thesis");
    await expect(thesis, `Thesis link at ${width}px`).toBeInViewport();
    const paragraph = page.getByText(token, { exact: true });
    const geometry = await paragraph.evaluate(element => {
      const parent = element.parentElement!;
      const range = document.createRange(); range.selectNodeContents(element); range.setStart(element.firstChild!, element.textContent!.length - 8);
      const tail = range.getBoundingClientRect(); const rect = parent.getBoundingClientRect();
      return { client: element.clientWidth, scroll: element.scrollWidth, tailRight: tail.right, right: rect.right, margin: parseFloat(getComputedStyle(element).marginTop) };
    });
    expect(geometry.scroll).toBeLessThanOrEqual(geometry.client + 1);
    expect(geometry.tailRight).toBeLessThanOrEqual(geometry.right + 1);
    expect(geometry.margin).toBeGreaterThan(8);
    const table = page.locator(".decision-summary table");
    await table.evaluate(element => { element.scrollLeft = element.scrollWidth; });
    const end = page.getByRole("cell", { name: "Endpoint", exact: true });
    const right = (await table.boundingBox())!;
    expect((await end.boundingBox())!.x).toBeLessThan(right.x + right.width);
  }
  await page.goto("/timelines/NVDA?node=full#missing-section");
  await expect(page.getByText("This chapter could not be located. Choose a section from Contents.")).toBeVisible();
  await page.goto("/timelines/NVDA?view=compare&compare=active:full&compare=active:increment#missing-section");
  await expect(page.getByText("This chapter could not be located. Choose a section from Contents.")).toBeVisible();
});
