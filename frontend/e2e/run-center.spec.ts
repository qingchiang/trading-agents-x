import { expect, test } from "@playwright/test";

const timestamp = "2026-07-24T00:00:00Z";

type MockRun = ReturnType<typeof makeRun>;

function makeRun(
  id: string,
  status: string,
  options: {
    ticker?: string;
    instrumentName?: string;
    trashedAt?: string | null;
    sourceRunId?: string | null;
  } = {},
) {
  return {
    id,
    source_run_id: options.sourceRunId ?? null,
    instrument_name: options.instrumentName ?? null,
    trashed_at: options.trashedAt ?? null,
    status,
    request: {
      ticker: options.ticker ?? "NVDA",
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
    },
    config_snapshot: {},
    attempt: 1,
    cancel_requested: false,
    error_code: null,
    error_message: null,
    metrics: {
      llm_calls: 0,
      tool_calls: 0,
      input_tokens: 0,
      output_tokens: 0,
      wall_time_seconds: 0,
      node_metrics: {},
    },
    created_at: timestamp,
    started_at: null,
    finished_at: status === "queued" || status === "running" ? null : timestamp,
    updated_at: timestamp,
  };
}

function result(id: string) {
  return {
    run_id: id,
    status: "succeeded",
    instrument: "NVDA",
    instrument_name: "NVIDIA Corporation",
    reports: {
      market: {
        analyst: "market",
        executive_summary: "Market evidence is balanced.",
        confidence: 0.7,
        claims: [
          {
            id: "market.claim_1",
            kind: "inference",
            statement: "The observed market signal is constructive.",
            implication: "Upside sensitivity remains relevant.",
            confidence: 0.7,
            evidence_refs: ["ev_0123456789ab"],
          },
        ],
        sections: [
          {
            id: "market_report",
            title: "Market report",
            narrative:
              "Market evidence cites ev_0123456789ab and remains balanced.",
            table_ids: [],
          },
        ],
        tables: [],
        catalysts: ["Demand improves"],
        risks: ["Demand slows"],
        invalidation_conditions: ["The observed trend reverses"],
        evidence_refs: ["ev_0123456789ab"],
        warnings: [
          {
            code: "evidence.partial",
            message: "Partial historical source",
            evidence_ref: "ev_0123456789ab",
            source: "fixture-feed",
          },
        ],
      },
      news: {
        analyst: "news",
        executive_summary: "No material change in the news path.",
        confidence: 0.6,
        claims: [
          {
            id: "news.claim_1",
            kind: "observation",
            statement: "The supplied snapshot contains no adverse event.",
            implication: "The news path does not override the market evidence.",
            confidence: 0.6,
            evidence_refs: ["ev_0123456789ab"],
          },
        ],
        sections: [
          {
            id: "news_report",
            title: "News report",
            narrative: "News evidence remains limited.",
            table_ids: [],
          },
        ],
        tables: [],
        catalysts: [],
        risks: ["Coverage remains limited"],
        invalidation_conditions: ["A material filing changes the event path"],
        evidence_refs: ["ev_0123456789ab"],
        warnings: [],
      },
    },
    decision: {
      rating: "Hold",
      confidence: 0.65,
      executive_summary: "The evidence supports a balanced research opinion.",
      thesis: "Evidence is balanced.",
      evidence_refs: ["ev_0123456789ab"],
      memory_refs: [],
      catalysts: ["Demand improves"],
      risks: ["Demand slows"],
      invalidation_conditions: ["New filing changes the thesis"],
      unresolved_questions: ["How durable is demand?"],
      time_horizon: "6-12 months",
      scenarios: [
        {
          kind: "base",
          core_assumptions: ["Demand remains stable"],
          outcome: "The balanced view persists.",
          evidence_refs: ["ev_0123456789ab"],
          valuation_range: null,
        },
        {
          kind: "bull",
          core_assumptions: ["Demand improves"],
          outcome: "Operating leverage improves.",
          evidence_refs: ["ev_0123456789ab"],
          valuation_range: null,
        },
        {
          kind: "bear",
          core_assumptions: ["Demand slows"],
          outcome: "The thesis weakens.",
          evidence_refs: ["ev_0123456789ab"],
          valuation_range: null,
        },
      ],
      valuation_assessment: null,
      market_reference_levels: [],
      risk_review_adjustments: [],
    },
    evidence: {
      version: "3",
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
          origins: [],
          provenance: { vendor: "fixture-feed" },
        },
      ],
      tables: [],
    },
    metrics: {
      llm_calls: 4,
      tool_calls: 3,
      input_tokens: 1200,
      output_tokens: 400,
      wall_time_seconds: 12.4,
      node_metrics: {},
    },
    warnings: [],
  };
}

function artifacts(id: string) {
  return [
    {
      id: "artifact-bull",
      run_id: id,
      attempt: 1,
      stage: "perspective",
      role: "bull",
      round: 0,
      prompt_version: "research-case-bull-v2",
      generation_method: "tool_call",
      created_at: timestamp,
      content: {
        role: "bull",
        executive_summary: "The constructive case remains conditional.",
        thesis: "Demand remains **constructive**.",
        arguments: [
          {
            id: "case.bull.argument_1",
            claim_ids: ["market.claim_1"],
            statement: "Demand remains constructive.",
            mechanism: "Demand supports operating leverage.",
            implication: "Upside sensitivity remains material.",
            confidence: 0.65,
            evidence_refs: ["ev_0123456789ab"],
          },
        ],
        strongest_counterarguments: ["Valuation risk is reflected."],
        fragile_assumptions: ["Demand remains resilient."],
        catalysts: ["Demand improves."],
        evidence_refs: ["ev_0123456789ab"],
        risks: ["Demand could slow."],
      },
    },
  ];
}

test("runs, templates, trash, and restores local research", async ({
  page,
}) => {
  test.setTimeout(60_000);
  const runs = new Map<string, MockRun>([
    [
      "run-report",
      makeRun("run-report", "succeeded", {
        ticker: "NVDA",
        instrumentName: "NVIDIA Corporation",
      }),
    ],
  ]);
  const purged = new Set<string>();
  let createdSequence = 0;
  let lastCreatePayload: Record<string, unknown> | null = null;

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;

    if (path === "/api/v1/health") {
      return route.fulfill({
        json: {
          status: "ok",
          database: "ok",
          queue: { queued: 0, running: 0, pending_outcomes: 1 },
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
            quick_reasoning_effort: "provider_default",
            deep_reasoning_effort: "provider_default",
            output_language: "zh-CN",
            lan_enabled: false,
            trash_retention_days: 30,
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
    if (path === "/api/v1/instruments/recent") {
      const active = [...runs.values()].filter(
        (run) => !run.trashed_at && !purged.has(run.id),
      );
      return route.fulfill({
        json: active.map((run) => ({
          ticker: run.request.ticker,
          instrument_name: run.instrument_name,
          last_used_at: run.updated_at,
        })),
      });
    }
    if (path === "/api/v1/runs" && request.method() === "GET") {
      const trashState = url.searchParams.get("trash_state") ?? "active";
      const q = (url.searchParams.get("q") ?? "").toLowerCase();
      const status = url.searchParams.get("status");
      const offset = Number(url.searchParams.get("offset") ?? 0);
      const limit = Number(url.searchParams.get("limit") ?? 20);
      const visible = [...runs.values()]
        .filter((run) => !purged.has(run.id))
        .filter((run) =>
          trashState === "trashed" ? run.trashed_at : !run.trashed_at,
        )
        .filter((run) => !status || run.status === status)
        .filter(
          (run) =>
            !q ||
            run.request.ticker.toLowerCase().includes(q) ||
            (run.instrument_name ?? "").toLowerCase().includes(q),
        );
      return route.fulfill({
        json: {
          items: visible.slice(offset, offset + limit),
          total: visible.length,
          limit,
          offset,
        },
      });
    }
    if (path === "/api/v1/runs" && request.method() === "POST") {
      lastCreatePayload = request.postDataJSON() as Record<string, unknown>;
      createdSequence += 1;
      const id = createdSequence === 1 ? "run-created" : "run-template";
      const created = makeRun(id, "queued", {
        ticker: String(lastCreatePayload.ticker),
        instrumentName:
          lastCreatePayload.ticker === "7203.T"
            ? "Toyota Motor Corporation"
            : null,
        sourceRunId:
          (lastCreatePayload.source_run_id as string | null) ?? null,
      });
      created.request = {
        ...created.request,
        ...(lastCreatePayload as typeof created.request),
      };
      runs.set(id, created);
      return route.fulfill({ status: 202, json: created });
    }
    if (path === "/api/v1/runs/trash") {
      const ids = (request.postDataJSON() as { run_ids: string[] }).run_ids;
      const changed: MockRun[] = [];
      for (const id of ids) {
        const current = runs.get(id);
        if (current && !current.trashed_at) {
          current.trashed_at = "2026-07-29T00:00:00Z";
          changed.push(current);
        }
      }
      return route.fulfill({
        json: { runs: ids.flatMap((id) => runs.get(id) ?? []), changed: changed.length },
      });
    }
    if (path === "/api/v1/runs/restore") {
      const ids = (request.postDataJSON() as { run_ids: string[] }).run_ids;
      let changed = 0;
      for (const id of ids) {
        const current = runs.get(id);
        if (current?.trashed_at) {
          current.trashed_at = null;
          changed += 1;
        }
      }
      return route.fulfill({
        json: { runs: ids.flatMap((id) => runs.get(id) ?? []), changed },
      });
    }
    if (path === "/api/v1/memory") {
      const report = runs.get("run-report");
      if (!report || report.trashed_at || purged.has(report.id)) {
        return route.fulfill({ json: [] });
      }
      return route.fulfill({
        json: [
          {
            run_id: report.id,
            ticker: report.request.ticker,
            instrument_name: report.instrument_name,
            market: "America/New_York",
            asset_type: "stock",
            analysis_date: "2026-07-24",
            decision: result(report.id).decision,
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
      return route.fulfill({
        json: artifactMatch[1] === "run-report"
          ? artifacts(artifactMatch[1])
          : [],
      });
    }
    const eventMatch = path.match(/^\/api\/v1\/runs\/([^/]+)\/events$/);
    if (eventMatch) {
      const run = runs.get(eventMatch[1]);
      const event = {
        run_id: eventMatch[1],
        sequence: 1,
        attempt: 1,
        event_type: `run.${run?.status ?? "failed"}`,
        node: null,
        payload: {},
        created_at: timestamp,
      };
      return route.fulfill({
        headers: { "Content-Type": "text/event-stream" },
        body: `id: 1\nevent: ${event.event_type}\ndata: ${JSON.stringify(event)}\n\n`,
      });
    }
    const actionMatch = path.match(
      /^\/api\/v1\/runs\/([^/]+)\/(cancel|retry)$/,
    );
    if (actionMatch) {
      const current = runs.get(actionMatch[1]);
      if (!current) {
        return route.fulfill({ status: 404, json: { detail: "Run not found" } });
      }
      current.status = actionMatch[2] === "cancel" ? "cancelled" : "queued";
      return route.fulfill({ json: current });
    }
    const detailMatch = path.match(/^\/api\/v1\/runs\/([^/]+)$/);
    if (detailMatch) {
      const id = detailMatch[1];
      if (purged.has(id) || !runs.has(id)) {
        return route.fulfill({ status: 404, json: { detail: "Run not found" } });
      }
      const run = runs.get(id)!;
      return route.fulfill({
        json: {
          run,
          result: id === "run-report" ? result(id) : null,
        },
      });
    }
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "运行概览" })).toBeVisible();
  await expect(page.getByText("NVIDIA Corporation")).toBeVisible();
  await page.getByLabel("界面语言").selectOption("en");

  await page.getByRole("link", { name: "New run", exact: true }).click();
  const ticker = page.getByLabel(/^Ticker/);
  await expect(ticker).toHaveAttribute("name", "ticker");
  await expect(ticker).toHaveAttribute("list", "recent-instruments");
  await ticker.fill("7203.T");
  await page.getByRole("button", { name: /Queue research/ }).click();
  await expect(page).toHaveURL(/\/runs\/run-created$/);
  await page.getByRole("button", { name: "Cancel" }).click();
  await expect(page.getByText("Cancelled")).toBeVisible();

  await page.getByRole("link", { name: "New from this run" }).click();
  await expect(ticker).toHaveValue("7203.T");
  await ticker.fill("MSFT");
  await page.getByRole("button", { name: /Queue research/ }).click();
  await expect(page).toHaveURL(/\/runs\/run-template$/);
  expect(lastCreatePayload).toMatchObject({
    ticker: "MSFT",
    source_run_id: "run-created",
  });

  await page.goto("/runs/run-report?view=deliberation");
  await expect(
    page.getByRole("heading", { name: "Bull and bear cases" }),
  ).toBeVisible();
  await expect(
    page.getByText("Demand remains constructive.").first(),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Open evidence ev_0123456789ab" })
    .first()
    .click();
  await expect(
    page.getByRole("heading", { name: "Price snapshot" }),
  ).toBeVisible();
  await page.getByRole("button", { name: /Return to deliberation/ }).click();
  await page.getByRole("tab", { name: "Reports" }).click();
  await expect(page.getByRole("heading", { name: "Market report" })).toBeVisible();

  await page.goto("/memory");
  await expect(page.getByText("NVIDIA Corporation")).toBeVisible();
  await page.getByText("Decision details").click();
  await expect(page.getByText("Demand improves")).toBeVisible();
  await page
    .getByRole("link", { name: "Open research decision", exact: true })
    .click();
  await expect(page).toHaveURL(/\/runs\/run-report\?view=decision/);

  await page.goto("/runs");
  const reportRow = page.getByRole("row").filter({ hasText: "NVDA" });
  await reportRow.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Move to Trash (1)" }).click();
  const trashDialog = page.getByRole("alertdialog", {
    name: "Move 1 selected run(s) to Trash?",
  });
  await expect(trashDialog).toContainText("permanent deletion");
  await trashDialog
    .getByRole("button", { name: "Move to Trash", exact: true })
    .click();
  await expect(page.getByText("Moved 1 run(s) to Trash.")).toBeVisible();

  await page.goto("/memory");
  await expect(page.getByText("No memory entries.")).toBeVisible();

  await page.goto("/runs?trash_state=trashed");
  await expect(page.getByText("Trash retention")).toBeVisible();
  const trashedRow = page.getByRole("row").filter({ hasText: "NVDA" });
  await trashedRow.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Restore selected (1)" }).click();
  await expect(page.getByText("Restored 1 run(s).")).toBeVisible();
  await page.goto("/memory");
  await expect(page.getByText("NVIDIA Corporation")).toBeVisible();

  const restored = runs.get("run-report")!;
  restored.trashed_at = "2026-06-01T00:00:00Z";
  purged.add("run-report");
  await page.goto("/runs/run-report");
  await expect(page.getByText("Run not found")).toBeVisible();

  await page.setViewportSize({ width: 1080, height: 1920 });
  purged.delete("run-report");
  restored.trashed_at = null;
  await page.goto("/runs/run-report?view=reports&report=market");
  const reportMaxHeight = await page
    .locator(".report-panel > .analyst-report")
    .evaluate((element) =>
      Number.parseFloat(getComputedStyle(element).maxHeight),
    );
  expect(reportMaxHeight).toBeGreaterThan(660);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/runs");
  const shell = page.locator(".app-shell");
  await page.getByRole("button", { name: "Open navigation" }).click();
  await expect(shell).toHaveClass(/sidebar-open/);
  await page.getByRole("link", { name: "Settings" }).click();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(shell).not.toHaveClass(/sidebar-open/);
});
