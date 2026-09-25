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

test("reads diagnostics directly and searches and downloads complete JSON beyond the rendered page", async ({ page }) => {
  const respond = workspaceFixture();
  const extra = Array.from({ length: 1800 }, (_, i) => i === 1799 ? '中文 <script>bad()</script> final-entry' : `entry-${i}`);
  await page.addInitScript(() => localStorage.setItem('tradingagents-locale', 'en'));
  await page.route('**/api/v1/**', route => {
    const url = new URL(route.request().url());
    const value = respond(url, route.request().method()) as Record<string, any>;
    if (url.pathname === '/api/v1/runs/full') {
      value.run.config_snapshot = { quick_binding: { model: 'quick-recorded', reasoning_effort: 'medium' }, deep_binding: { model: 'deep-recorded', reasoning_effort: 'high' }, temperature: 0, extra };
      value.run.metrics = { llm_calls: 0, input_tokens: 1250 };
    }
    return route.fulfill({ json: value });
  });
  await page.goto('/runs/full?view=diagnostics');
  await expect(page.getByRole('heading', { name: 'Run metrics and diagnostics' })).toBeVisible();
  await expect(page.locator('.numeric-audit-appendix')).toHaveCount(0);
  await expect(page.getByText('quick-recorded', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Raw record: Configuration snapshot', exact: true }).click();
  const viewer = page.getByRole('region', { name: 'Raw record: Configuration snapshot', exact: true });
  await expect(viewer.locator('.json-line')).toHaveCount(500);
  await viewer.getByRole('searchbox', { name: 'Search JSON' }).fill('中文');
  await expect(viewer.locator('mark')).toHaveText('中文');
  await expect(viewer.locator('.json-line-current')).toBeInViewport();
  await expect(viewer.locator('script')).toHaveCount(0);
  const downloadPromise = page.waitForEvent('download');
  await viewer.getByRole('button', { name: 'Download JSON' }).click();
  const download = await downloadPromise;
  const stream = await download.createReadStream();
  const data = JSON.parse(await new Promise<string>((resolve, reject) => {
    let text = ""; const decoder = new TextDecoder();
    stream.on('data', (chunk: Uint8Array) => { text += decoder.decode(chunk, { stream: true }); });
    stream.on('end', () => resolve(text + decoder.decode())); stream.on('error', reject);
  }));
  expect(data.extra).toHaveLength(1800);
  expect(data.extra[1799]).toBe('中文 <script>bad()</script> final-entry');
  expect(data.temperature).toBe(0);
  for (const width of [390, 768, 1080, 1440, 1920, 2560]) {
    await page.setViewportSize({ width, height: 1000 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  }
});

test('reads generic diagnostic history without old audit UI at every viewport', async ({ page }) => {
  const respond = workspaceFixture();
  await page.addInitScript(() => localStorage.setItem('tradingagents-locale', 'en'));
  await page.route('**/api/v1/**', route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/v1/runs/increment/events') {
      const events = ['decision.reference_omitted', 'node.numeric_audit_degraded', 'run.succeeded'].map((event_type, index) => ({
        run_id: 'increment', sequence: index + 1, attempt: 1, event_type, node: 'incremental.synthesis.decision',
        payload: { field_path: 'market_reference_levels.0', validation_issues: ['reference.refs_invalid'] },
        created_at: '2026-07-24T12:00:00Z',
      }));
      return route.fulfill({ contentType: 'text/event-stream', body: events.map(event =>
        `id: ${event.sequence}\nevent: message\ndata: ${JSON.stringify(event)}\n\n`).join('') });
    }
    return route.fulfill({ json: respond(url, route.request().method()) });
  });
  for (const [width, height] of [[390,844], [768,1024], [1080,1920], [1440,1000], [1920,1080], [2560,1440]]) {
    await page.setViewportSize({ width, height });
    await page.goto('/runs/increment?view=diagnostics');
    await expect(page.locator('.numeric-audit-appendix')).toHaveCount(0);
    await expect(page.getByRole('option', { name: 'node.numeric_audit_degraded' })).toBeAttached();
    await page.getByRole('button', { name: 'Raw record: 1 · decision.reference_omitted', exact: true }).click();
    await expect(page.getByRole('region', { name: 'Raw record: 1 · decision.reference_omitted', exact: true })).toContainText('reference.refs_invalid');
    expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
  }
});
