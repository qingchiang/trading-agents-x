import { expect, test } from "@playwright/test";
import { workspaceFixture } from "./fixtures/workspace";

test.beforeEach(async ({ page }) => {
  const respond = workspaceFixture();
  await page.addInitScript(() => localStorage.setItem("tradingagents-locale", "en"));
  await page.route("**/api/v1/**", route => route.fulfill({ json: respond(new URL(route.request().url()), route.request().method()) }));
});

test("keeps reading and history reachable as the viewport changes", async ({ page }) => {
  for (const [width, height] of [[390,844], [768,1024], [1080,1920], [1440,1000], [1920,1080], [2560,1440]]) {
    await page.setViewportSize({ width, height });
    await page.goto('/timelines/NVDA?node=full');
    const summary = page.getByRole('heading', { name: 'Executive summary', exact: true });
    await expect(summary).toBeVisible();
    expect((await summary.boundingBox())!.y).toBeLessThan(height - 90);
    expect((await page.locator('.research-header').boundingBox())!.height).toBeLessThan(290);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
    const workspace = page.locator('.research-workspace');
    const available = (await workspace.boundingBox())!.width;
    await expect(workspace).toHaveClass(new RegExp(available >= 1100 ? 'wide' : 'compact'));
    await page.goto('/timelines/NVDA?node=full&view=reports&report=market');
    await expect(page.locator('#run-view-reports .markdown')).toBeVisible();
    await page.evaluate(() => scrollTo(0, 550));
    const toolbar = page.locator('.reading-toolbar');
    await expect.poll(async () => (await toolbar.boundingBox())!.y).toBeLessThanOrEqual(56);
    if (available < 1100) {
      const trigger = page.getByRole('button', { name: 'Research history', exact: true });
      const position = await page.evaluate(() => scrollY);
      await trigger.click();
      const selected = page.locator('.workspace-auxiliary .history-select[aria-current]');
      await expect.poll(async () => {
        const row = await selected.boundingBox(); const rail = await page.locator('.workspace-auxiliary').boundingBox();
        return !!row && !!rail && row.y >= rail.y && row.y + row.height <= rail.y + rail.height;
      }).toBe(true);
      await page.keyboard.press('Escape');
      await expect(trigger).toBeFocused();
      expect(Math.abs(await page.evaluate(() => scrollY) - position)).toBeLessThan(2);
    }
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.evaluate(() => { document.documentElement.style.zoom = '1.25'; });
  await expect(page.locator('.research-workspace')).toHaveClass(/compact/);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
});

test("puts current tasks before expandable cycle history without dropping members", async ({ page }) => {
  await page.goto('/runs');
  const group = page.locator('.task-group').first();
  await expect(group.getByText('Running', { exact: true })).toBeVisible();
  await expect(group.locator('.task-row:visible')).toHaveCount(3);
  await group.getByText('Historical tasks (23)', { exact: true }).click();
  await expect(group.locator('.task-row:visible')).toHaveCount(26);
  await group.getByText('Historical tasks (23)', { exact: true }).click();
  await expect(group.locator('.task-row:visible')).toHaveCount(3);
  await page.goto('/runs?q=NVDA');
  await expect(page.locator('.task-group').first().locator('.task-row:visible')).toHaveCount(26);
});

test("restores cycle history from the URL and previews the whole cycle", async ({ page }) => {
  await page.goto('/runs?expanded_group=full');
  const group = page.locator('.task-group').first();
  await expect(group.locator('.task-row:visible')).toHaveCount(26);
  await group.getByText('Historical tasks (23)', { exact: true }).click();
  await expect(page).not.toHaveURL(/expanded_group=full/);
  await page.goBack();
  await expect(group.locator('.task-row:visible')).toHaveCount(26);
  await group.getByRole('button', { name: 'Manage cycle', exact: true }).click();
  await group.getByRole('button', { name: 'Move Cycle to Trash' }).click();
  const dialog = page.getByRole('alertdialog');
  await expect(dialog).toContainText('Affected records: 25');
  await expect(dialog.getByRole('button', { name: 'Confirm Trash' })).toBeDisabled();
  await expect(dialog).not.toContainText('pending');
});

test("shows library fields and the three-part research form on a phone", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/timelines');
  const row = page.locator('.research-library-table tbody tr').first();
  await expect(row.locator('td').nth(2)).toContainText('2026-07-25');
  await expect(row.getByRole('button', { name: 'Research history' })).toBeVisible();
  expect(await page.locator('.table-wrap').evaluate(element => element.scrollWidth - element.clientWidth)).toBeLessThanOrEqual(1);
  await page.screenshot({ path: '../.scratch/research-workspace-v3/phase4-screenshots/390-library.png' });
  await page.goto('/runs/new');
  await page.locator('#new-run-ticker').fill('NVDA');
  await expect(page.locator('#new-run-analysis-date')).toBeEnabled();
  const form = page.locator('.run-form');
  await expect(form.locator(':scope > .panel')).toHaveCount(3);
  await expect(form.getByRole('button', { name: /Standard/ })).toHaveAttribute('aria-pressed', 'true');
  expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  await page.screenshot({ path: '../.scratch/research-workspace-v3/phase4-screenshots/390-new-research.png' });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/runs');
  await expect(page.getByRole('button', { name: 'Manage cycle' })).toHaveCount(2);
  await page.screenshot({ path: '../.scratch/research-workspace-v3/phase4-screenshots/1440-tasks.png' });
});
