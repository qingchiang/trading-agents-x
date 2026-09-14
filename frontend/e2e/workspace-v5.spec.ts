import { expect, test } from "@playwright/test";
import { workspaceFixture } from "./fixtures/workspace";

test("keeps native navigation keys and separates cycle controls from research links", async ({ page }) => {
  const respond = workspaceFixture();
  await page.addInitScript(() => localStorage.setItem("tradingagents-locale", "en"));
  await page.route("**/api/v1/**", route => route.fulfill({ json: respond(new URL(route.request().url()), route.request().method()) }));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/timelines/NVDA?node=full");
  const overview = page.locator('.reading-toolbar nav').getByRole('link', { name: 'Overview', exact: true });
  await expect(page.locator(".scenario-card").last()).toBeAttached();
  await page.evaluate(() => document.fonts.ready);
  await overview.focus();
  const initial = page.url();
  await page.keyboard.press('End');
  await expect.poll(() => page.evaluate(() => Math.abs(document.documentElement.scrollHeight - innerHeight - scrollY))).toBeLessThanOrEqual(2);
  expect(page.url()).toBe(initial);
  await page.keyboard.press('Home');
  await expect.poll(() => page.evaluate(() => scrollY)).toBe(0);
  await page.keyboard.press('ArrowRight');
  expect(page.url()).toBe(initial);
  await expect(overview).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: 'Research reports', exact: true })).toBeFocused();
  const history = page.locator('.history-cycle').first();
  await history.getByRole('heading').click();
  await expect(history.locator('.history-select').first()).toBeVisible();
  await history.getByRole('button', { name: /Collapse cycle/ }).click();
  await expect(history.locator('.history-select').first()).toBeHidden();
  await history.getByRole('button', { name: /Expand cycle/ }).click();
  await history.locator('.history-select').first().click();
  await expect(page).toHaveURL(/node=full/);
  await page.getByRole('button', { name: 'More', exact: true }).click();
  await page.getByRole('button', { name: 'Compare research', exact: true }).click();
  const choices = page.locator('.comparison-choice input');
  await choices.nth(0).check(); await choices.nth(1).check();
  await expect(choices.nth(2)).toBeDisabled();
  await expect(page.getByText('Two studies selected. Remove one to choose another.')).toBeVisible();
});

test("exposes instrument links before hovering and aligns endpoint fields on stock and benchmark rows", async ({ page }) => {
  const respond = workspaceFixture();
  await page.route("**/api/v1/**", route => route.fulfill({ json: respond(new URL(route.request().url()), route.request().method()) }));
  for (const width of [390, 1080, 1440, 2560]) {
    await page.setViewportSize({ width, height: 1000 });
    for (const [path, selector] of [['/', '.research-summary a .instrument-primary-name'], ['/timelines', '.research-library-table a .instrument-primary-name']]) {
      await page.goto(path);
      await expect(page.locator(selector).first()).toBeVisible();
      expect(await page.locator(selector).first().evaluate(e => getComputedStyle(e).textDecorationLine)).toContain('underline');
    }
    await page.goto('/timelines/NVDA?node=increment&view=brief');
    const row = page.locator('.performance-row').first();
    await expect(row.locator('.performance-endpoints')).toBeVisible();
    const endpoints = (await row.locator('.performance-endpoints').boundingBox())!;
    const sessions = (await row.locator('.performance-sessions').boundingBox())!;
    expect(sessions.y).toBeGreaterThan(endpoints.y);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  }
});
