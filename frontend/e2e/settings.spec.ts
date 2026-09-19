import { expect, test, type Page } from "@playwright/test";
import fixture from "./fixtures/configuration.json" with { type: "json" };
type ConfigurationView = Omit<typeof fixture.view, "connections"> & { connections: Record<string, (typeof fixture.view.connections)[keyof typeof fixture.view.connections]> };
type ConfigurationSchema = typeof fixture.schema;

async function settingsServer(page: Page, conflict = false) {
  const view = structuredClone(fixture.view) as ConfigurationView;
  const schema = fixture.schema as ConfigurationSchema;
  const secrets: Record<string, string> = {};
  let saved: Record<string, unknown> = {};
  let previewed = false;
  const id = view.values.quick_connection_id!;
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
      if (conflict) { conflict = false; view.revision++; return route.fulfill({ status: 409, json: { error: { code: "configuration_revision_conflict", message: "Conflict" } } }); }
      const payload = route.request().postDataJSON();
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
    await page.getByRole("searchbox").fill("OPENAI_API_KEY");
    await page.locator(".configuration-search-results a").filter({ hasText: "OPENAI_API_KEY" }).first().click();
    const editor = page.locator(".connection-editor");
    await editor.locator("#credential-api_key").fill("browser-test-secret");
    await editor.locator(".sticky-save button").first().click();
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
  await page.getByRole("button", { name: "Load latest values for comparison" }).click();
  await page.getByRole("button", { name: "Keep my edits and use the latest revision" }).click();
  await expect(page.locator("#setting-output_language")).toHaveValue("zh-CN");
  await page.locator("#configuration-research > .configuration-actions button").first().click();
  await expect.poll(() => server.view.values.output_language).toBe("zh-CN");
  await page.locator('.configuration-nav a[href="/settings/connections"]').click();
  await page.getByRole("button", { name: "Edit", exact: true }).click();
  await page.getByRole("button", { name: "Copy", exact: true }).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe("offline-import-key");
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain("offline-import-key");
});
