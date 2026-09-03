import { expect, test } from "@playwright/test";

const serverMarketDate = "2026-09-04";

for (const timezoneId of ["Pacific/Kiritimati", "Pacific/Honolulu"]) {
  test.describe(`New Run in ${timezoneId}`, () => {
    test.use({ timezoneId });

    test("uses the same server market date in the form and final POST", async ({ page }) => {
      let postedAnalysisDate: unknown;
      await page.clock.setFixedTime(new Date("2026-09-04T10:30:00Z"));
      await page.route("**/api/v1/**", async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        const path = url.pathname;
        if (path === "/api/v1/capabilities") {
          return route.fulfill({ json: {
            profiles: ["fast", "standard", "deep"],
            analysts: ["market", "social", "news", "fundamentals"],
            output_languages: ["en", "zh-CN", "ja"],
            providers: { openai: { label: "OpenAI", api_key_required: true,
              api_key_configured: true, configured: true, selectable: true,
              unavailable_reason: null, model_discovery_supported: true } },
            defaults: { profile: "standard", llm_provider: "openai",
              quick_model: "gpt-5.4-mini", deep_model: "gpt-5.5",
              quick_reasoning_effort: "provider_default",
              deep_reasoning_effort: "provider_default", output_language: "en",
              lan_enabled: false, trash_retention_days: 30 },
          } });
        }
        if (path === "/api/v1/providers/openai/models") {
          return route.fulfill({ json: { provider: "openai", models: [
            { id: "gpt-5.4-mini", label: "Quick", compatibility: "supported",
              reasoning_efforts: ["provider_default"], default_roles: ["quick"] },
            { id: "gpt-5.5", label: "Deep", compatibility: "supported",
              reasoning_efforts: ["provider_default"], default_roles: ["deep"] },
          ], source: "fixture", fetched_at: "2026-09-04T10:30:00Z",
          stale: false, warning: null } });
        }
        if (path === "/api/v1/instruments/recent") {
          return route.fulfill({ json: [] });
        }
        if (path === "/api/v1/instruments/NVDA/analysis-cutoff-context") {
          return route.fulfill({ json: {
            instrument: "NVDA",
            market_timezone: "America/New_York",
            market_date: serverMarketDate,
            max_analysis_date: serverMarketDate,
            observed_at: "2026-09-04T10:30:00Z",
            valid_until: "2026-09-05T04:00:00Z",
          } });
        }
        if (path === "/api/v1/timelines/NVDA/baseline-candidates") {
          return route.fulfill({ json: {
            instrument: "NVDA",
            before: url.searchParams.get("before"),
            items: [],
          } });
        }
        if (path === "/api/v1/runs" && request.method() === "POST") {
          postedAnalysisDate = (request.postDataJSON() as { analysis_date: unknown })
            .analysis_date;
          return route.fulfill({ status: 202, json: { id: `run-${timezoneId}` } });
        }
        return route.fulfill({ status: 404, json: { detail: "Not found" } });
      });

      await page.goto("/runs/new");
      await page.locator("#new-run-ticker").fill("NVDA");
      const date = page.locator("#new-run-analysis-date");
      await expect(date).toHaveValue(serverMarketDate);
      await expect(date).toHaveAttribute("max", serverMarketDate);
      await page.getByRole("button", { name: /Queue research|加入研究队列|リサーチをキューへ/ }).click();

      await expect.poll(() => postedAnalysisDate).toBe(serverMarketDate);
    });
  });
}
