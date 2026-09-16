import { expect, test } from "@playwright/test";
import fixture from "./fixtures/configuration.json" with { type: "json" };

for (const locale of ["en", "zh-CN", "ja"] as const) {
  test(`configuration, credentials and responsive editing (${locale})`, async ({
    page,
  }, info) => {
    const view = structuredClone(fixture.view);
    const secrets: Record<string, string> = {};
    let saved: Record<string, unknown> = {};
    await page.addInitScript(
      (value) => localStorage.setItem("tradingagents-locale", value),
      locale,
    );
    await page.route("**/api/v1/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/api/v1/settings/schema")
        return route.fulfill({ json: fixture.schema });
      if (path === "/api/v1/capabilities")
        return route.fulfill({ json: { providers: {} } });
      if (path === "/api/v1/settings/import/apply") {
        view.initialized = true;
        view.revision++;
        return route.fulfill({ json: view });
      }
      if (path === "/api/v1/settings/credentials/reveal")
        return route.fulfill({
          json: { value: secrets[route.request().postDataJSON().name] },
          headers: { "Cache-Control": "no-store" },
        });
      if (path === "/api/v1/settings" && route.request().method() === "PATCH") {
        const payload = route.request().postDataJSON();
        saved = payload.values ?? {};
        Object.assign(view.values, saved);
        for (const [name, value] of Object.entries(payload.credentials ?? {})) {
          secrets[name] = value as string;
          view.credentials[name as keyof typeof view.credentials] =
            value !== null;
        }
        view.revision++;
        return route.fulfill({ json: view });
      }
      if (path === "/api/v1/settings") return route.fulfill({ json: view });
      return route.fulfill({ json: {} });
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/settings");
    await expect(page.locator(".configuration-search")).toBeVisible();
    const group = page.locator("#configuration-research");
    await group.locator("#setting-output_language").fill("ja");
    await group.locator(".configuration-actions > button").first().click();
    await expect.poll(() => saved.output_language).toBe("ja");
    await page.getByRole("searchbox").fill("OPENAI_API_KEY");
    const provider = page
      .locator(".configuration-provider")
      .filter({ has: page.locator("#credential-OPENAI_API_KEY") });
    await provider
      .locator("#credential-OPENAI_API_KEY")
      .fill("browser-test-secret");
    await page
      .locator("#configuration-providers > .configuration-actions > button")
      .first()
      .click();
    await expect.poll(() => secrets.OPENAI_API_KEY).toBe("browser-test-secret");
    await provider
      .locator(".configuration-credential .configuration-actions > button")
      .first()
      .click();
    await expect(
      provider.getByRole("textbox", { name: "OPENAI_API_KEY", exact: true }),
    ).toHaveValue("browser-test-secret");
    expect(
      await page.evaluate(() => JSON.stringify(localStorage)),
    ).not.toContain("browser-test-secret");
    await provider
      .locator(".configuration-credential .configuration-actions > button")
      .first()
      .click();
    await page.getByRole("searchbox").fill("data_vendors_by_market");
    await page
      .locator("#configuration-sources summary")
      .filter({ hasText: /^\.T$/ })
      .click();
    const chain = page
      .locator("#configuration-sources fieldset")
      .filter({ has: page.locator("legend", { hasText: /^core_stock_apis$/ }) })
      .first();
    await chain
      .locator(".configuration-list-row")
      .first()
      .getByRole("button")
      .nth(1)
      .click();
    await page
      .locator("#configuration-sources > .configuration-actions > button")
      .first()
      .click();
    await expect
      .poll(
        () =>
          (
            saved.data_vendors_by_market as Record<
              string,
              Record<string, string>
            >
          )?.[".T"].core_stock_apis,
      )
      .toBe("yfinance,jquants");
    for (const width of [390, 1280]) {
      await page.setViewportSize({ width, height: 900 });
      await page.getByRole("searchbox").fill("");
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth - innerWidth,
        ),
      ).toBeLessThanOrEqual(1);
      await page.screenshot({
        path: info.outputPath(`settings-${locale}-${width}.png`),
      });
    }
  });
}

test("preview, revision conflict and explicit credential copy", async ({
  page,
  context,
}) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.addInitScript(() =>
    localStorage.setItem("tradingagents-locale", "en"),
  );
  const view = structuredClone(fixture.view);
  let conflict = true;
  let previewed = false;
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/v1/settings/schema")
      return route.fulfill({ json: fixture.schema });
    if (path === "/api/v1/capabilities")
      return route.fulfill({ json: { providers: {} } });
    if (path === "/api/v1/settings/import/preview") {
      expect(route.request().postDataJSON().primary).toContain(
        "offline-import-key",
      );
      previewed = true;
      return route.fulfill({
        json: {
          revision: view.revision,
          fingerprint: "preview-fingerprint",
          values: { output_language: "ja" },
          credentials: { OPENAI_API_KEY: true },
          issues: [],
          conflicts: [],
        },
      });
    }
    if (path === "/api/v1/settings/import/apply") {
      expect(previewed).toBe(true);
      expect(route.request().postDataJSON().fingerprint).toBe(
        "preview-fingerprint",
      );
      view.initialized = true;
      view.credentials.OPENAI_API_KEY = true;
      view.values.output_language = "ja";
      view.revision++;
      return route.fulfill({ json: view });
    }
    if (path === "/api/v1/settings/credentials/reveal")
      return route.fulfill({ json: { value: "offline-import-key" } });
    if (path === "/api/v1/settings" && route.request().method() === "PATCH") {
      if (conflict) {
        conflict = false;
        view.revision++;
        return route.fulfill({
          status: 409,
          json: {
            error: {
              code: "configuration_revision_conflict",
              message: "Conflict",
            },
          },
        });
      }
      expect(route.request().postDataJSON().revision).toBe(view.revision);
      Object.assign(view.values, route.request().postDataJSON().values);
      view.revision++;
      return route.fulfill({ json: view });
    }
    if (path === "/api/v1/settings") return route.fulfill({ json: view });
    return route.fulfill({ json: {} });
  });
  await page.goto("/settings");
  await page
    .getByLabel(".env file", { exact: true })
    .setInputFiles("e2e/fixtures/import-settings.txt");
  await page.getByRole("button", { name: "Preview import" }).click();
  await expect(
    page.getByRole("button", { name: "Apply reviewed import" }),
  ).toBeVisible();
  await expect(page.locator("body")).not.toContainText("offline-import-key");
  await page.getByRole("button", { name: "Apply reviewed import" }).click();
  await expect(page.locator("#setting-output_language")).toHaveValue("ja");
  await page.locator("#setting-output_language").fill("zh-CN");
  await page
    .locator("#configuration-research .configuration-actions button")
    .first()
    .click();
  await expect(page.getByRole("alert")).toContainText(
    "Your edits are retained",
  );
  await page.getByRole("button", { name: "Reload latest settings" }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await expect(page.locator("#setting-output_language")).toHaveValue("zh-CN");
  await page
    .locator("#configuration-research .configuration-actions button")
    .first()
    .click();
  await expect.poll(() => view.values.output_language).toBe("zh-CN");
  await page.getByRole("searchbox").fill("OPENAI_API_KEY");
  await page
    .locator(".configuration-provider")
    .filter({ has: page.locator("#credential-OPENAI_API_KEY") })
    .getByRole("button", { name: "Copy", exact: true })
    .click();
  await expect
    .poll(() => page.evaluate(() => navigator.clipboard.readText()))
    .toBe("offline-import-key");
  expect(await page.evaluate(() => JSON.stringify(localStorage))).not.toContain(
    "offline-import-key",
  );
});
