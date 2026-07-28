import { expect, test } from "@playwright/test";

const timestamp = "2026-07-24T00:00:00Z";

function run(id: string, status: string) {
  return {
    id,
    parent_run_id: id === "run-2" ? "run-1" : null,
    status,
    request: {
      ticker: "NVDA",
      analysis_date: "2026-07-24",
      asset_type: "stock",
      profile: "standard",
      analysts: ["market", "news"],
      llm_provider: "openai",
      quick_model: "gpt-5.4-mini",
      deep_model: "gpt-5.5",
      quick_reasoning_effort: "provider_default",
      deep_reasoning_effort: "provider_default",
      output_language: "en",
      provenance: true,
    },
    config_snapshot: {},
    attempt: 1,
    cancel_requested: false,
    metrics: {
      llm_calls: 0,
      tool_calls: 0,
      input_tokens: 0,
      output_tokens: 0,
      wall_time_seconds: 0,
      node_wall_times: {},
    },
    created_at: timestamp,
    started_at: null,
    finished_at: null,
    updated_at: timestamp,
  };
}

function result(id: string) {
  return {
    run_id: id,
    status: "succeeded",
    instrument: "NVDA",
    reports: {
      market: {
        analyst: "market",
        summary: "Market summary",
        claims: [],
        confidence: 0.7,
        evidence_refs: ["ev_0123456789ab"],
        warnings: ["Partial historical source"],
        narrative: "# Market report\n\nMarket evidence.",
      },
      news: {
        analyst: "news",
        summary: "News summary",
        claims: [],
        confidence: 0.6,
        evidence_refs: ["ev_0123456789ab"],
        warnings: [],
        narrative: "# News report\n\nNews evidence.",
      },
    },
    decision: {
      rating: "Hold",
      confidence: 0.65,
      thesis: "Evidence is balanced.",
      evidence_refs: ["ev_0123456789ab"],
      catalysts: ["Demand improves"],
      risks: ["Demand slows"],
      invalidation_conditions: ["New filing changes the thesis"],
      time_horizon: "6-12 months",
    },
    evidence: {
      version: "1",
      instrument: "NVDA",
      analysis_date: "2026-07-24",
      sealed_at: timestamp,
      digest: "fixture-digest",
      items: [
        {
          ref: "ev_0123456789ab",
          source: "fixture-feed",
          evidence_type: "Price snapshot",
          requested_date: "2026-07-24",
          effective_date: "2026-07-24",
          available_at: timestamp,
          content: "The close was **100 USD**.",
          value: 100,
          unit: "USD",
          quality: "high",
          fallback: false,
          provenance: { vendor: "fixture-feed" },
        },
      ],
    },
    metrics: {
      llm_calls: 4,
      tool_calls: 3,
      input_tokens: 1200,
      output_tokens: 400,
      wall_time_seconds: 12.4,
      node_wall_times: {},
    },
    warnings: [],
  };
}

test("runs the local research workflow across UI locales", async ({ page }) => {
  let current = run("run-1", "queued");
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path === "/api/v1/health") {
      return route.fulfill({
        json: {
          status: "ok",
          database: "ok",
          queue: { queued: 1, running: 0, pending_outcomes: 1 },
          version: "0.5.0",
        },
      });
    }
    if (path === "/api/v1/capabilities") {
      return route.fulfill({
        json: {
          profiles: ["fast", "standard", "deep"],
          analysts: ["market", "social", "news", "fundamentals"],
          output_languages: ["en", "zh-CN", "ja"],
          providers: {
            openai: {
              label: "OpenAI",
              api_key_required: true,
              api_key_configured: true,
              configured: true,
              selectable: true,
              unavailable_reason: null,
              model_discovery_supported: true,
            },
          },
          defaults: {
            profile: "standard",
            llm_provider: "openai",
            quick_model: "gpt-5.4-mini",
            deep_model: "gpt-5.5",
            quick_reasoning_effort: null,
            deep_reasoning_effort: null,
            output_language: "zh-CN",
            provenance: false,
            lan_enabled: false,
          },
        },
      });
    }
    if (path === "/api/v1/providers/openai/models") {
      return route.fulfill({
        json: {
          provider: "openai",
          models: [
            {
              id: "gpt-5.4-mini",
              label: "GPT quick",
              compatibility: "supported",
              reasoning_efforts: ["provider_default", "low"],
              default_roles: ["quick"],
            },
            {
              id: "gpt-5.5",
              label: "GPT deep",
              compatibility: "supported",
              reasoning_efforts: ["provider_default", "high"],
              default_roles: ["deep"],
            },
          ],
          source: "live",
          fetched_at: timestamp,
          stale: false,
          warning: null,
        },
      });
    }
    if (path === "/api/v1/runs" && request.method() === "GET") {
      return route.fulfill({ json: [current] });
    }
    if (path === "/api/v1/runs" && request.method() === "POST") {
      current = run("run-1", "queued");
      return route.fulfill({ status: 202, json: current });
    }
    if (path === "/api/v1/memory") {
      return route.fulfill({
        json: [
          {
            run_id: "run-report",
            ticker: "NVDA",
            market: "America/New_York",
            asset_type: "stock",
            analysis_date: "2026-07-24",
            decision: result("run-report").decision,
            outcome: {
              status: "resolved",
              benchmark: "SPY",
              observation_start: "2026-07-25",
              observation_end: "2026-08-01",
              holding_intervals: 5,
              raw_return: 0.08,
              alpha_return: 0.03,
            },
            reflection: "The evidence was directionally useful.",
          },
        ],
      });
    }
    const artifactMatch = path.match(
      /^\/api\/v1\/runs\/([^/]+)\/artifacts$/,
    );
    if (artifactMatch) {
      const id = artifactMatch[1];
      return route.fulfill({
        json:
          id === "run-report"
            ? [
                {
                  id: "artifact-bull",
                  run_id: id,
                  attempt: 1,
                  stage: "perspective",
                  role: "bull",
                  round: 0,
                  schema_version: "1",
                  created_at: timestamp,
                  content: {
                    role: "bull",
                    thesis: "Demand remains **constructive**.",
                    claim_rebuttals: ["Valuation risk is reflected."],
                    evidence_refs: ["ev_0123456789ab"],
                    new_evidence_refs: [],
                    risks: ["Demand could slow."],
                  },
                },
              ]
            : [],
      });
    }
    const eventMatch = path.match(/^\/api\/v1\/runs\/([^/]+)\/events$/);
    if (eventMatch) {
      const id = eventMatch[1];
      const first = {
        run_id: id,
        sequence: 1,
        attempt: 1,
        event_type: "run.queued",
        node: null,
        payload: {},
        created_at: timestamp,
      };
      const second = {
        ...first,
        sequence: 2,
        event_type: id === "run-report" ? "run.succeeded" : "run.cancelled",
      };
      return route.fulfill({
        headers: { "Content-Type": "text/event-stream" },
        body:
          `id: 1\nevent: ${first.event_type}\ndata: ${JSON.stringify(first)}\n\n` +
          `id: 2\nevent: ${second.event_type}\ndata: ${JSON.stringify(second)}\n\n`,
      });
    }
    const actionMatch = path.match(
      /^\/api\/v1\/runs\/([^/]+)\/(cancel|retry|rerun)$/,
    );
    if (actionMatch) {
      const action = actionMatch[2];
      if (action === "cancel") current = run(actionMatch[1], "cancelled");
      if (action === "retry") current = run(actionMatch[1], "queued");
      if (action === "rerun") current = run("run-2", "failed");
      return route.fulfill({ json: current });
    }
    const detailMatch = path.match(/^\/api\/v1\/runs\/([^/]+)$/);
    if (detailMatch) {
      const id = detailMatch[1];
      if (id === "run-report") {
        return route.fulfill({
          json: { run: run(id, "succeeded"), result: result(id) },
        });
      }
      return route.fulfill({ json: { run: current, result: null } });
    }
    return route.fulfill({ status: 404, json: { error: "not found" } });
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "运行概览" })).toBeVisible();
  await page.getByLabel("界面语言").selectOption("ja");
  await expect(
    page.getByRole("heading", { name: "ダッシュボード" }),
  ).toBeVisible();
  await page.getByLabel("UI 言語").selectOption("en");

  await page.locator("nav").getByRole("link", { name: /New run/ }).click();
  await page.getByLabel(/^Ticker/).fill("NVDA");
  await page.getByRole("button", { name: /Queue research/ }).click();
  await expect(page).toHaveURL(/\/runs\/run-1$/);
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByText("Cancelled")).toBeVisible();
  await page.getByRole("button", { name: "Rerun" }).click();
  await expect(page).toHaveURL(/\/runs\/run-2$/);
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByText("Queued")).toBeVisible();

  await page.goto("/runs/run-report");
  await page.getByRole("tab", { name: "Deliberation" }).click();
  await expect(page.getByText("constructive")).toBeVisible();
  await page
    .getByRole("button", {
      name: "Open evidence ev_0123456789ab",
    })
    .click();
  await expect(
    page.getByRole("heading", { name: "Price snapshot" }),
  ).toBeVisible();
  await page.getByRole("tab", { name: "Reports" }).click();
  await expect(page.getByRole("heading", { name: "Market report" })).toBeVisible();
  await page.getByRole("button", { name: "news" }).click();
  await expect(page.getByRole("heading", { name: "News report" })).toBeVisible();
  await page.reload();
  await page.getByRole("tab", { name: "Agent timeline" }).click();
  await expect(page.getByText(/#2/)).toBeVisible();

  await page.getByRole("link", { name: "Memory" }).click();
  await expect(
    page.getByText("The evidence was directionally useful."),
  ).toBeVisible();
});
