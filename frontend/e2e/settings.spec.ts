import { expect, test, type Page } from "@playwright/test";
import fixture from "./fixtures/configuration.json" with { type: "json" };
type ConfigurationView = Omit<typeof fixture.view, "connections"> & { connections: Record<string, (typeof fixture.view.connections)[keyof typeof fixture.view.connections]> };
type ConfigurationSchema = typeof fixture.schema;

async function settingsServer(page: Page, conflict = false, shared?: ConfigurationView) {
  const view = shared ?? structuredClone(fixture.view) as ConfigurationView;
  const schema = fixture.schema as ConfigurationSchema;
  const secrets: Record<string, string> = {};
  let saved: Record<string, unknown> = {};
  let previewed = false;
  const id = view.values.models.quick.connection_id;
  secrets[`${id}:api_key`] = "offline-key";
  await page.route("**/api/v1/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/settings/schema") return route.fulfill({ json: schema });
    if (path === "/api/v1/capabilities") return route.fulfill({ json: { providers: {} } });
    if (path === "/api/v1/settings/credentials/reveal") {
      const body = route.request().postDataJSON();
      return route.fulfill({ json: { value: secrets[`${body.connection_id}:${body.name}`] }, headers: { "Cache-Control": "no-store" } });
    }
    if (path === "/api/v1/settings/import/preview") {
      expect(route.request().postDataJSON().primary).toContain("offline-import-key");
      previewed = true;
      return route.fulfill({ json: { revision: view.revision, fingerprint: "reviewed", values: { output_language: "ja" }, credentials: { OPENAI_API_KEY: true }, issues: [], conflicts: [] } });
    }
    if (path === "/api/v1/settings/import/apply") {
      expect(previewed).toBe(true);
      expect(route.request().postDataJSON().fingerprint).toBe("reviewed");
      view.values.output_language = "ja"; view.revision++;
      secrets[`${id}:api_key`] = "offline-import-key";
      return route.fulfill({ json: view });
    }
    if (path === "/api/v1/settings" && route.request().method() === "PATCH") {
      const payload = route.request().postDataJSON();
      if (conflict || payload.revision !== view.revision) { if (conflict) view.revision++; conflict = false; return route.fulfill({ status: 409, json: { error: { code: "configuration_revision_conflict", message: "Conflict" } } }); }
      expect(payload.revision).toBe(view.revision);
      saved = payload.values ?? {}; Object.assign(view.values, saved);
      for (const change of payload.connection_changes ?? []) {
        const current = view.connections![change.id];
        for (const [field, value] of Object.entries(change.credentials ?? {})) {
          secrets[`${change.id}:${field}`] = value as string;
          current.credentials[field] = value !== null;
        }
        if (change.name) current.connection.name = change.name;
      }
      view.revision++; return route.fulfill({ json: view });
    }
    if (path === "/api/v1/settings") return route.fulfill({ json: view });
    return route.fulfill({ json: {} });
  });
  return { view, secrets, id, saved: () => saved };
}

test("connection editors have interior spacing and usable controls", async ({ page }) => {
  await settingsServer(page);
  await page.addInitScript(() => localStorage.setItem("tradingagents-locale", "en"));
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto("/settings");
    await page.getByRole("button", { name: "Edit", exact: true }).click();
    const editor = page.locator(".connection-editor");
    await page.screenshot({ path: test.info().outputPath(`connection-editor-${width}.png`), fullPage: true });
    const layout = await editor.evaluate(element => {
      const style = getComputedStyle(element);
      const button = element.querySelector('.configuration-credential button')!;
      return { padding: parseFloat(style.paddingLeft), buttonHeight: button.getBoundingClientRect().height, overflow: document.documentElement.scrollWidth - innerWidth };
    });
    expect(layout.padding).toBeGreaterThanOrEqual(16);
    expect(layout.buttonHeight).toBeGreaterThanOrEqual(36);
    expect(layout.overflow).toBeLessThanOrEqual(1);
  }
});

for (const locale of ["en", "zh-CN", "ja"] as const) {
  test(`categorized settings and private credentials (${locale})`, async ({ page }) => {
    await page.addInitScript(value => localStorage.setItem("tradingagents-locale", value), locale);
    const server = await settingsServer(page);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/settings");
    await expect(page.locator(".connection-card")).toHaveCount(1);
    await expect(page.locator("#configuration-research")).toHaveCount(0);
    await page.locator('.configuration-nav a[href="/settings/research"]').click();
    await page.locator("#setting-output_language").fill("ja");
    // Drafts survive category navigation.
    await page.locator('.configuration-nav a[href="/settings/connections"]').click();
    await page.locator('.configuration-nav a[href="/settings/research"]').click();
    await expect(page.locator("#setting-output_language")).toHaveValue("ja");
    await page.locator("#configuration-research > .configuration-actions button").first().click();
    await expect.poll(() => server.saved().output_language).toBe("ja");
    await page.getByRole("searchbox").fill("api_key");
    await page.locator('.configuration-search-results a[href$="#credential-api_key"]').first().click();
    const editor = page.locator(".connection-editor");
    await editor.locator("#credential-api_key").fill("browser-test-secret");
    await editor.locator(".settings-save-bar button").first().click();
    await expect.poll(() => server.secrets[`${server.id}:api_key`]).toBe("browser-test-secret");
    await editor.locator(".configuration-credential .configuration-actions button").first().click();
    await expect(editor.locator('input[value="browser-test-secret"]')).toBeVisible();
    expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain("browser-test-secret");
    await page.locator('.configuration-nav a[href="/settings/data"]').click();
    await page.locator("#setting-data_vendors_by_market > summary").click();
    await page.locator("#configuration-sources summary").filter({ hasText: /^\.T$/ }).click();
    const chain = page.locator("#configuration-sources details").filter({ has: page.locator("summary", { hasText: /^\.T$/ }) }).locator("fieldset").filter({ has: page.locator("legend", { hasText: /^core_stock_apis$/ }) }).first();
    await chain.locator(".configuration-list-row").first().getByRole("button").nth(1).click();
    await page.locator("#configuration-sources > .configuration-actions button").first().click();
    await expect.poll(() => (server.saved().data_vendors_by_market as Record<string, Record<string, string>>)?.[".T"].core_stock_apis).toBe("yfinance,jquants");
    for (const width of [390, 1280]) {
      await page.setViewportSize({ width, height: 900 });
      await expect.poll(() => page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    }
    await page.screenshot({ path: test.info().outputPath(`settings-${locale}.png`), fullPage: true });
  });
}

test("reviewed import, conflict resolution and clipboard remain explicit", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.addInitScript(() => localStorage.setItem("tradingagents-locale", "en"));
  const server = await settingsServer(page, true);
  await page.goto("/settings/storage");
  await page.locator(".configuration-import > summary").click();
  await page.getByLabel(".env file", { exact: true }).setInputFiles("e2e/fixtures/import-settings.txt");
  await page.getByRole("button", { name: "Preview import" }).click();
  await expect(page.locator("body")).not.toContainText("offline-import-key");
  await page.getByRole("button", { name: "Apply reviewed import" }).click();
  await page.locator('.configuration-nav a[href="/settings/research"]').click();
  await page.locator("#setting-output_language").fill("zh-CN");
  await page.locator("#configuration-research > .configuration-actions button").first().click();
  await expect(page.getByRole("alert")).toContainText("Your edits are retained");
  await page.getByRole("button", { name: "Apply choices to draft" }).click();
  await expect(page.locator("#setting-output_language")).toHaveValue("zh-CN");
  await page.locator("#configuration-research > .configuration-actions button").first().click();
  await expect.poll(() => server.view.values.output_language).toBe("zh-CN");
  await page.locator('.configuration-nav a[href="/settings/connections"]').click();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page.getByRole("button", { name: "Copy", exact: true }).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe("offline-import-key");
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain("offline-import-key");
});

for (const locale of ["en", "zh-CN", "ja"]) {
  test(`search focus, fixed save actions and active navigation (${locale})`, async ({ page }) => {
    const server = await settingsServer(page);
    server.view.connections.second = structuredClone(server.view.connections[server.id]);
    server.view.connections.second.connection.id = "second";
    server.view.connections.second.connection.name = "Second endpoint";
    await page.addInitScript(value => localStorage.setItem("tradingagents-locale", value), locale);
    for (const width of [1440, 390]) {
      await page.setViewportSize({ width, height: 844 });
      await page.goto("/settings/research");
      const bar = page.locator(".settings-save-bar");
      await expect(bar).toBeVisible();
      await expect(bar.getByRole("button").first()).toBeDisabled();
      await expect.poll(async () => bar.evaluate(el => { const r = el.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight + 1; })).toBe(true);
      await page.getByRole("searchbox").fill("analysts");
      await page.locator('.configuration-search-results a[href$="#setting-analysts"]').click();
      await expect(page.locator("#setting-analysts")).toBeFocused();
      await page.getByRole("searchbox").fill("global_news_queries");
      await page.locator('.configuration-search-results a[href$="#setting-global_news_queries"]').click();
      await expect(page.locator("#setting-global_news_queries")).toBeFocused();
      await page.getByRole("searchbox").fill("FRED_API_KEY");
      await page.locator('.configuration-search-results a[href$="#credential-FRED_API_KEY"]').click();
      await expect(page.locator("#credential-FRED_API_KEY")).toBeFocused();
      await page.getByRole("searchbox").fill("base_url");
      await expect(page.locator(".configuration-search-results a")).toHaveCount(2);
      await page.locator('.configuration-search-results a[href*="connection=second"]').click();
      await expect(page.locator("#connection-name")).toHaveValue("Second endpoint");
      await expect(page.locator("#connection-base_url")).toBeFocused();
      // Keyboard operation keeps a visible focus target and enables save.
      await page.locator("#connection-name").focus();
      await page.keyboard.press("End"); await page.keyboard.type(" edited");
      await expect(bar.getByRole("button").first()).toBeEnabled();
      await page.keyboard.press("Tab");
      await expect(page.locator("#connection-enabled")).toBeFocused();
      await page.locator('.configuration-nav a[href="/settings/storage"]').click();
      const active = page.locator('.configuration-nav [aria-current="page"]');
      await expect.poll(async () => active.evaluate(el => { const item = el.getBoundingClientRect(); const nav = el.parentElement!.getBoundingClientRect(); return item.left >= nav.left - 1 && item.right <= nav.right + 1; })).toBe(true);
      await expect(page.locator(".configuration-deployment")).not.toHaveAttribute("open");
      await page.locator("#setting-trash_retention_days").focus();
      await expect.poll(async () => page.locator("#setting-trash_retention_days").evaluate(el => el.getBoundingClientRect().bottom <= document.querySelector(".settings-save-bar")!.getBoundingClientRect().top)).toBe(true);
      expect(await page.evaluate(() => document.documentElement.scrollWidth - innerWidth)).toBeLessThanOrEqual(1);
      await page.screenshot({ path: test.info().outputPath(`settings-save-${locale}-${width}.png`), fullPage: false });
    }
  });
}


test("two pages save independent fields without overwriting each other", async ({ page, context }) => {
  await context.addInitScript(() => localStorage.setItem("tradingagents-locale", "en"));
  const server = await settingsServer(page);
  const other = await context.newPage();
  await settingsServer(other, false, server.view);
  await Promise.all([page.goto("/settings/research"), other.goto("/settings/research")]);
  await page.locator("#setting-output_language").fill("ja");
  await other.locator("#setting-temperature").fill("0.4");
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect.poll(() => server.view.values.output_language).toBe("ja");
  await other.getByRole("button", { name: "Save changes" }).click();
  await other.getByRole("button", { name: "Apply choices to draft" }).click();
  await expect(other.locator("#setting-output_language")).toHaveValue("ja");
  await other.getByRole("button", { name: "Save changes" }).click();
  await expect.poll(() => server.view.values.temperature).toBe(0.4);
  expect(server.view.values.output_language).toBe("ja");
});
