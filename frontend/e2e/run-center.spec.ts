import { expect, test } from "@playwright/test";

const timestamp = "2026-07-24T00:00:00Z";

type MockRun = ReturnType<typeof makeRun>;

function makeRun(
  id: string,
  status: string,
  options: {
    ticker?: string;
    instrumentName?: string;
    instrumentLocalName?: string;
    trashedAt?: string | null;
    sourceRunId?: string | null;
  } = {},
) {
  return {
    id,
    source_run_id: options.sourceRunId ?? null,
    instrument_name: options.instrumentName ?? null,
    instrument_local_name: options.instrumentLocalName ?? null,
    research_schema_version: status === "succeeded" ? "1" : null,
    information_cutoff_at: status === "succeeded" ? "2026-07-24T23:59:59Z" : null,
    method_snapshot: status === "succeeded" ? { llm_provider: "openai" } : null,
    research_rating: status === "succeeded" ? "Hold" : null,
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
      research_kind: "full",
      full_baseline_run_id: null,
      make_primary: true,
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
        markdown:
          "# Market report\n\nMarket evidence is balanced.[^ev_0123456789ab]\n\n" +
          "| Signal | Reading |\n|---|---:|\n| Close | 100 USD |\n\n" +
          "Supporting market context remains evidence-bound.\n\n".repeat(60) +
          "## Risk lens\n\nDemand sensitivity remains material.\n\n" +
          "Supporting risk context remains evidence-bound.\n\n".repeat(30),
        report_sections: [
          {
            id: "market.market-report",
            title: "Market report",
            anchor: "market-report",
            source_refs: ["ev_0123456789ab"],
          },
          {
            id: "market.risk-lens",
            title: "Risk lens",
            anchor: "risk-lens",
            source_refs: ["ev_0123456789ab"],
          },
        ],
        confidence: 0.7,
        key_claims: [
          {
            id: "market.claim_1",
            section_id: "market.market-report",
            kind: "inference",
            importance: "primary",
            statement: "The observed market signal is constructive.",
            implication: "Upside sensitivity remains relevant.",
            confidence: 0.7,
            evidence_refs: ["ev_0123456789ab"],
          },
        ],
        source_refs: ["ev_0123456789ab"],
        audit_status: "complete",
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
        markdown:
          "# News report\n\nNo material change in the news path.[^ev_0123456789ab]",
        report_sections: [
          {
            id: "news.news-report",
            title: "News report",
            anchor: "news-report",
            source_refs: ["ev_0123456789ab"],
          },
        ],
        confidence: 0.6,
        key_claims: [
          {
            id: "news.claim_1",
            section_id: "news.news-report",
            kind: "observation",
            importance: "supporting",
            statement: "The supplied snapshot contains no adverse event.",
            implication: "The news path does not override the market evidence.",
            confidence: 0.6,
            evidence_refs: ["ev_0123456789ab"],
          },
        ],
        source_refs: ["ev_0123456789ab"],
        audit_status: "complete",
        warnings: [],
      },
    },
    decision: {
      rating: "Hold",
      confidence: 0.65,
      executive_summary: "The evidence supports a balanced research opinion.",
      thesis: "Evidence is balanced.",
      evidence_refs: ["ev_0123456789ab"],
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
          reference_ranges: [],
        },
        {
          kind: "bull",
          core_assumptions: ["Demand improves"],
          outcome: "Operating leverage improves.",
          evidence_refs: ["ev_0123456789ab"],
          reference_ranges: [],
        },
        {
          kind: "bear",
          core_assumptions: ["Demand slows"],
          outcome: "The thesis weakens.",
          evidence_refs: ["ev_0123456789ab"],
          reference_ranges: [],
        },
      ],
      valuation_assessment: null,
      market_reference_levels: [],
      risk_review_adjustments: [],
      calculation_records: Array.from({ length: 16 }, (_, index) => ({
        id: `calc_fixture_${index + 1}`,
        formula: "observed_value",
        inputs: { observed_value: 100 + index },
        input_evidence_refs: ["ev_0123456789ab"],
        result: 100 + index,
        unit: "USD",
        as_of_date: "2026-07-24",
        limitations: [],
        decision_uses: [
          {
            component_path: "thesis",
            label: `Observed anchor ${index + 1}`,
          },
        ],
      })),
    },
    evidence: {
      version: "5",
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
      generation_method: "markdown_audited",
      created_at: timestamp,
      content: {
        role: "bull",
        markdown:
          "Demand remains **constructive**.[^ev_0123456789ab]\n\n" +
          "| Case input | Assessment |\n|---|---|\n| Demand | Resilient |",
        focus_claim_ids: ["market.claim_1"],
        report_section_refs: ["market.market-report"],
      },
    },
  ];
}

type TimelineNodeFixture = {
  id: string;
  cycle_id: string;
  research_kind: "full" | "incremental";
  [key: string]: unknown;
};

function cycleTimeline(
  instrument: string,
  nodes: TimelineNodeFixture[],
  primaryCycleId: string | null,
  timelineWarning = false,
) {
  const grouped = new Map<string, TimelineNodeFixture[]>();
  for (const node of nodes) {
    const items = grouped.get(node.cycle_id) ?? [];
    items.push(node);
    grouped.set(node.cycle_id, items);
  }
  const cycles = [...grouped.entries()].map(([cycleId, items]) => {
    const baseline = items.find((item) => item.research_kind === "full") ?? {
      ...items[0],
      id: `fixture-baseline-${cycleId}`,
      research_kind: "full" as const,
      full_baseline_run_id: null,
      is_cycle_head: false,
      is_primary: cycleId === primaryCycleId,
    };
    const increments = items.filter((item) => item.research_kind === "incremental");
    return {
      id: cycleId,
      baseline,
      increments,
      head_run_id: increments.at(-1)?.id ?? baseline.id,
      is_primary: cycleId === primaryCycleId,
      cycle_warning: Boolean(baseline.cycle_warning) ||
        increments.some((item) => item.cycle_warning === true),
    };
  });
  return {
    timeline: {
      instrument,
      primary_cycle_id: primaryCycleId,
      timeline_warning: timelineWarning,
      cycles,
      cycle_total: cycles.length,
      cycle_limit: 12,
      cycle_offset: 0,
    },
    primary_cycle_candidates: [],
  };
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
    [
      "run-daiichi",
      makeRun("run-daiichi", "succeeded", {
        ticker: "4568.T",
        instrumentName: "DAIICHI SANKYO COMPANY LIMITED",
        instrumentLocalName: "第一三共",
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
          queue: { queued: 0, running: 0 },
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
    const cutoffContextMatch = path.match(
      /^\/api\/v1\/instruments\/([^/]+)\/analysis-cutoff-context$/,
    );
    if (cutoffContextMatch) {
      const instrument = decodeURIComponent(cutoffContextMatch[1]).toUpperCase();
      return route.fulfill({ json: {
        instrument,
        market_timezone: instrument.endsWith(".T") ? "Asia/Tokyo" : "America/New_York",
        market_date: "2026-07-24",
        max_analysis_date: "2026-07-24",
        observed_at: timestamp,
        valid_until: "2999-01-01T00:00:00Z",
      } });
    }
    const timelineMatch = path.match(/^\/api\/v1\/timelines\/([^/]+)$/);
    if (timelineMatch) {
      const instrument = decodeURIComponent(timelineMatch[1]);
      const nodes: TimelineNodeFixture[] = [...runs.values()]
        .filter((run) => run.request.ticker === instrument)
        .filter((run) => run.status === "succeeded" && run.research_schema_version)
        .map((run) => ({
          id: run.id,
          cycle_id: run.id,
          instrument,
          analysis_date: run.request.analysis_date,
          research_schema_version: run.research_schema_version,
          information_cutoff_at: run.information_cutoff_at,
          method_snapshot: run.method_snapshot,
          research_kind: "full" as const,
          full_baseline_run_id: null,
          is_baseline_compatible: true,
          is_cycle_head: true,
          is_primary: run.id === "run-report",
          is_active: !run.trashed_at,
          decision: result(run.id).decision,
        }));
      return route.fulfill({ json: cycleTimeline(instrument, nodes, nodes[0]?.id ?? null) });
    }
    const baselineCandidatesMatch = path.match(
      /^\/api\/v1\/timelines\/([^/]+)\/baseline-candidates$/,
    );
    if (baselineCandidatesMatch) {
      return route.fulfill({
        json: {
          instrument: decodeURIComponent(baselineCandidatesMatch[1]),
          before: url.searchParams.get("before"),
          items: [],
        },
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
    const evidenceMatch = path.match(
      /^\/api\/v1\/runs\/([^/]+)\/evidence$/,
    );
    if (evidenceMatch) {
      if (evidenceMatch[1] !== "run-report") {
        return route.fulfill({
          status: 409,
          json: {
            error: {
              code: "evidence_not_sealed",
              message: "Evidence is not sealed yet.",
            },
          },
        });
      }
      return route.fulfill({ json: result(evidenceMatch[1]).evidence });
    }
    const eventMatch = path.match(/^\/api\/v1\/runs\/([^/]+)\/events$/);
    if (eventMatch) {
      const run = runs.get(eventMatch[1]);
      const events = eventMatch[1] === "run-report"
        ? [
            ...Array.from({ length: 24 }, (_, index) => ({
              run_id: eventMatch[1],
              sequence: index + 1,
              attempt: 1,
              event_type: "node.completed",
              node: `fixture.stage.${index}`,
              payload: {},
              created_at: new Date(
                Date.parse(timestamp) + index * 1_000,
              ).toISOString(),
            })),
            {
              run_id: eventMatch[1],
              sequence: 25,
              attempt: 1,
              event_type: "run.succeeded",
              node: null,
              payload: {},
              created_at: new Date(Date.parse(timestamp) + 24_000).toISOString(),
            },
          ]
        : [{
            run_id: eventMatch[1],
            sequence: 1,
            attempt: 1,
            event_type: `run.${run?.status ?? "failed"}`,
            node: null,
            payload: {},
            created_at: timestamp,
          }];
      return route.fulfill({
        headers: { "Content-Type": "text/event-stream" },
        body: events
          .map((event) =>
            `id: ${event.sequence}\nevent: ${event.event_type}\ndata: ${JSON.stringify(event)}\n\n`,
          )
          .join(""),
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
    const creationTemplateMatch = path.match(
      /^\/api\/v1\/runs\/([^/]+)\/creation-template$/,
    );
    if (creationTemplateMatch) {
      const source = runs.get(creationTemplateMatch[1]);
      if (!source) {
        return route.fulfill({ status: 404, json: { detail: "Run not found" } });
      }
      return route.fulfill({ json: {
        run_id: source.id,
        status: source.status,
        research_kind: source.request.research_kind ?? "full",
        full_baseline_run_id: source.request.full_baseline_run_id ?? null,
        instrument_name: source.instrument_name,
        instrument_local_name: source.instrument_local_name,
        request: source.request,
      } });
    }
    if (detailMatch) {
      const id = detailMatch[1];
      if (purged.has(id) || !runs.has(id)) {
        return route.fulfill({ status: 404, json: { detail: "Run not found" } });
      }
      const run = runs.get(id)!;
      const completedResult = result(id);
      const partialResult =
        id === "run-report"
          ? completedResult
          : {
              ...completedResult,
              status: run.status,
              reports: {},
              decision: null,
              evidence: null,
              warnings: [],
            };
      return route.fulfill({
        json: {
          run,
          result: partialResult,
          attempts: [
            {
              attempt: 1,
              status: run.status,
              resume_count: 0,
              metrics: run.metrics,
              started_at: null,
              finished_at: null,
              error_code: null,
            },
          ],
          evidence_status:
            id === "run-report"
              ? {
                  status: "sealed",
                  digest: completedResult.evidence?.digest ?? null,
                  item_count: completedResult.evidence?.items.length ?? 0,
                  table_count: completedResult.evidence?.tables.length ?? 0,
                  sealed_attempt: 1,
                  sealed_at: timestamp,
                }
              : {
                  status: "pending",
                  digest: null,
                  item_count: 0,
                  table_count: 0,
                  sealed_attempt: null,
                  sealed_at: null,
                },
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
  await expect(
    page.locator("header").getByText("Cancelled", { exact: true }),
  ).toBeVisible();

  await page.getByRole("link", { name: "Reuse configuration for Full Research" }).click();
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
    page.getByRole("dialog", { name: "Source details" }),
  ).toBeVisible();
  await expect(page).toHaveURL(/view=deliberation/);
  await expect(
    page.getByRole("heading", { name: "fixture-feed" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await page.getByRole("tab", { name: "Reports" }).click();
  await expect(page.getByRole("heading", { name: "Market report" })).toBeVisible();

  await page.goto("/runs/run-report?view=decision");
  await expect(
    page.getByRole("tab", { name: "Overview", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await expect(
    page.getByRole("heading", { name: "Executive summary", exact: true }),
  ).toBeVisible();
  const decisionWidth = await page.locator(".decision-hero").evaluate((hero) => {
    const summary = hero.querySelector<HTMLElement>(".decision-summary");
    if (!summary) throw new Error("decision summary not found");
    return {
      hero: hero.getBoundingClientRect().width,
      summary: summary.getBoundingClientRect().width,
    };
  });
  expect(decisionWidth.summary).toBeGreaterThan(decisionWidth.hero * 0.7);

  await expect(page.getByText("Run metrics and diagnostics")).toHaveCount(0);
  await page.getByText("Decision-critical calculation audit").click();
  await expect(page.locator(".calculation-record-list article")).toHaveCount(16);
  await expect(page.getByText("calc_fixture_1", { exact: true })).toBeHidden();
  await expect(
    page
      .locator(".numeric-calculation-detail")
      .first()
      .getByText("observed_value", { exact: true })
      .first(),
  ).toBeHidden();

  await page.getByRole("tab", { name: "Activity" }).click();
  await expect(page.getByText("Run metrics and diagnostics")).toBeVisible();
  await expect(page.getByText("Attempt metrics")).toBeHidden();
  await expect(page.getByRole("button", { name: "Latest first" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  const attemptBody = page.locator(".activity-attempt-body").first();
  await expect(attemptBody).toHaveAttribute("tabindex", "0");
  const attemptScroll = await attemptBody.evaluate((element) => ({
    maxHeight: getComputedStyle(element).maxHeight,
    overflowY: getComputedStyle(element).overflowY,
  }));
  expect(attemptScroll.maxHeight).not.toBe("none");
  expect(attemptScroll.overflowY).toBe("auto");
  const attemptOverflow = await attemptBody.evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
  }));
  expect(attemptOverflow.scrollHeight).toBeGreaterThan(attemptOverflow.clientHeight);
  await attemptBody.hover();
  await page.mouse.wheel(0, 500);
  await expect
    .poll(() => attemptBody.evaluate((element) => element.scrollTop))
    .toBeGreaterThan(0);
  const attemptPanel = page.locator(".activity-attempt").first();
  await expect(attemptPanel.getByText(/Technical events \(25\)/)).toHaveCount(1);
  await expect(attemptPanel.getByText("Audit details", { exact: true })).toHaveCount(0);
  await expect(attemptPanel.locator(".activity-node-key").first()).toHaveText("run.lifecycle");
  await page.getByRole("button", { name: "Earliest first" }).click();
  await expect(attemptPanel.locator(".activity-node-key").first()).toHaveText("fixture.stage.0");
  expect(
    await page.evaluate(() =>
      localStorage.getItem("tradingagents-timeline-order"),
    ),
  ).toBe("oldest");
  await page.reload();
  await expect(page.getByRole("button", { name: "Earliest first" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  await page.getByRole("button", { name: "Latest first" }).click();

  await page.goto("/runs/run-daiichi?view=decision");
  const detailIdentity = page.locator(".run-title .instrument-identity");
  await expect(detailIdentity).toContainText("第一三共");
  const detailGeometry = await detailIdentity.evaluate((element) => {
    const primary = element.querySelector<HTMLElement>(".instrument-primary-name");
    const alternate = element.querySelector<HTMLElement>(".instrument-alternate-name");
    if (!primary || !alternate) throw new Error("detail names not found");
    return {
      primaryClientWidth: primary.clientWidth,
      primaryScrollWidth: primary.scrollWidth,
      alternateClientWidth: alternate.clientWidth,
      alternateScrollWidth: alternate.scrollWidth,
      gap:
        alternate.getBoundingClientRect().left -
        primary.getBoundingClientRect().right,
    };
  });
  expect(detailGeometry.primaryScrollWidth).toBeLessThanOrEqual(
    detailGeometry.primaryClientWidth + 1,
  );
  expect(detailGeometry.alternateScrollWidth).toBeLessThanOrEqual(
    detailGeometry.alternateClientWidth + 1,
  );
  expect(detailGeometry.gap).toBeGreaterThanOrEqual(6);
  expect(detailGeometry.gap).toBeLessThanOrEqual(10);
  const detailKindBadge = page
    .locator(".run-title")
    .getByRole("button", { name: "Full research" });
  const badgeGeometry = await detailKindBadge.evaluate((element) => ({
    clientHeight: element.clientHeight,
    scrollHeight: element.scrollHeight,
  }));
  expect(badgeGeometry.scrollHeight).toBeLessThanOrEqual(
    badgeGeometry.clientHeight,
  );
  await expect(page.locator(".run-heading .subtitle").first()).toHaveText(
    "2026-07-24 · Attempt 1",
  );

  await page.goto("/timelines/NVDA");
  await expect(page.getByText("Primary Cycle")).toBeVisible();
  await expect(page.getByRole("link", { name: "Open research detail →" })).toHaveAttribute(
    "href",
    "/runs/run-report",
  );
  const timelineSummary = page.locator(".timeline-decision-summary").first();
  const timelineWidth = await timelineSummary.evaluate((element) => ({
    summary: element.getBoundingClientRect().width,
    card: element.closest(".research-node-card")?.getBoundingClientRect().width ?? 0,
  }));
  expect(timelineWidth.summary).toBeGreaterThan(timelineWidth.card * 0.85);

  await page.goto("/runs");
  await expect(page.getByRole("columnheader", { name: "Actions" })).toBeVisible();
  const runsTable = page.locator(".runs-table");
  await expect(
    runsTable.getByRole("link", { name: "Research Timeline" }),
  ).toHaveCount(0);
  const tickerColumnRatio = await runsTable.evaluate((table) => {
    const tickerHeader = table.querySelectorAll("th")[1];
    if (!tickerHeader) throw new Error("ticker column not found");
    return tickerHeader.getBoundingClientRect().width / table.getBoundingClientRect().width;
  });
  expect(tickerColumnRatio).toBeGreaterThan(0.25);
  expect(tickerColumnRatio).toBeLessThan(0.30);
  const openAction = page
    .getByRole("row")
    .filter({ hasText: "NVDA" })
    .getByRole("link", { name: "Open" });
  await expect(openAction).toHaveClass(/compact-button/);
  expect(
    await openAction.evaluate(
      (element) => element.getBoundingClientRect().height,
    ),
  ).toBeLessThanOrEqual(34);
  const runsSearch = page.locator("#runs-search");
  const runsKind = page.locator("#runs-kind");
  const applyFilters = page.getByRole("button", { name: "Apply", exact: true });
  const [searchBox, kindBox, applyBox] = await Promise.all([
    runsSearch.boundingBox(),
    runsKind.boundingBox(),
    applyFilters.boundingBox(),
  ]);
  expect(searchBox).not.toBeNull();
  expect(kindBox).not.toBeNull();
  expect(applyBox).not.toBeNull();
  expect(Math.abs(
    ((searchBox?.y ?? 0) + (searchBox?.height ?? 0)) -
    ((applyBox?.y ?? 0) + (applyBox?.height ?? 0)),
  )).toBeLessThanOrEqual(1);
  expect(applyBox?.x ?? 0).toBeGreaterThan((kindBox?.x ?? 0) + (kindBox?.width ?? 0));

  const daiichiIdentity = page
    .getByRole("row")
    .filter({ hasText: "4568.T" })
    .locator(".instrument-identity");
  const nameGeometry = await daiichiIdentity.evaluate((element) => {
    const primary = element.querySelector<HTMLElement>(".instrument-primary-name");
    const alternate = element.querySelector<HTMLElement>(".instrument-alternate-name");
    if (!primary || !alternate) throw new Error("instrument names not found");
    return {
      primaryWidth: primary.getBoundingClientRect().width,
      alternateWidth: alternate.getBoundingClientRect().width,
      nameGap:
        alternate.getBoundingClientRect().left -
        primary.getBoundingClientRect().right,
      primaryClientWidth: primary.clientWidth,
      primaryScrollWidth: primary.scrollWidth,
      alternateClientWidth: alternate.clientWidth,
      alternateScrollWidth: alternate.scrollWidth,
    };
  });
  expect(nameGeometry.nameGap).toBeGreaterThanOrEqual(6);
  expect(nameGeometry.nameGap).toBeLessThanOrEqual(10);
  expect(nameGeometry.primaryScrollWidth).toBeLessThanOrEqual(nameGeometry.primaryClientWidth + 1);
  expect(nameGeometry.alternateScrollWidth).toBeLessThanOrEqual(nameGeometry.alternateClientWidth + 1);

  const daiichiKindBadge = page
    .getByRole("row")
    .filter({ hasText: "4568.T" })
    .getByRole("button", { name: "Full research" });
  await daiichiKindBadge.click();
  const configuration = page.getByRole("tooltip", { name: "Research configuration" });
  await expect(configuration).toContainText("gpt-5.4-mini");
  await expect(configuration).toContainText("gpt-5.5");
  await page.keyboard.press("Escape");
  await expect(configuration).toBeHidden();

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

  await page.goto("/runs?trash_state=trashed");
  await expect(page.getByText("Trash retention")).toBeVisible();
  const trashedRow = page.getByRole("row").filter({ hasText: "NVDA" });
  await trashedRow.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Restore selected (1)" }).click();
  await expect(page.getByText("Restored 1 run(s).")).toBeVisible();
  const restored = runs.get("run-report")!;
  restored.trashed_at = "2026-06-01T00:00:00Z";
  purged.add("run-report");
  await page.goto("/runs/run-report");
  await expect(page.getByText("Run not found")).toBeVisible();

  await page.setViewportSize({ width: 1080, height: 1920 });
  purged.delete("run-report");
  restored.trashed_at = null;
  await page.goto("/runs/run-report?view=reports&report=market");
  await expect(
    page.getByRole("navigation", { name: "Report section navigation" }),
  ).toBeVisible();
  const reportScroller = page.locator(".report-panel .analyst-report");
  const expandedReportWidth = await reportScroller.evaluate(
    (element) => element.getBoundingClientRect().width,
  );
  await page.getByRole("button", { name: "Close navigation" }).click();
  await expect(
    page.getByRole("navigation", { name: "Report section navigation" }),
  ).toBeHidden();
  const compactNavigation = page.getByRole("button", {
    name: "Open navigation",
  });
  await expect(compactNavigation).toHaveText("☰");
  expect(
    await compactNavigation.evaluate(
      (element) => element.getBoundingClientRect().width,
    ),
  ).toBeLessThanOrEqual(36);
  expect(
    await reportScroller.evaluate(
      (element) => element.getBoundingClientRect().width,
    ),
  ).toBe(expandedReportWidth);
  await compactNavigation.click();
  await page
    .getByRole("navigation", { name: "Report section navigation" })
    .getByRole("button", { name: "Risk lens" })
    .click();
  const riskOffset = await page
    .getByRole("heading", { name: "Risk lens" })
    .evaluate((heading) => {
      const scroller = heading.closest(".analyst-report");
      if (!scroller) throw new Error("report scroller not found");
      return (
        heading.getBoundingClientRect().top -
        scroller.getBoundingClientRect().top
      );
    });
  expect(riskOffset).toBeGreaterThanOrEqual(12);
  expect(riskOffset).toBeLessThanOrEqual(24);
  const reportMaxHeight = await page
    .locator(".report-panel .analyst-report")
    .evaluate((element) =>
      Number.parseFloat(getComputedStyle(element).maxHeight),
    );
  expect(reportMaxHeight).toBeGreaterThan(660);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/runs/run-report?view=reports&report=market");
  await expect(page.getByLabel("Jump to section")).toBeVisible();
  await expect(
    page.getByRole("navigation", { name: "Report section navigation" }),
  ).toBeHidden();
  await page.goto("/runs");
  const shell = page.locator(".app-shell");
  await page.getByRole("button", { name: "Open navigation" }).click();
  await expect(shell).toHaveClass(/sidebar-open/);
  await page.getByRole("link", { name: "Settings" }).click();
  await expect(page).toHaveURL(/\/settings$/);
  await expect(shell).not.toHaveClass(/sidebar-open/);

  for (const [locale, latestLabel, earliestLabel] of [
    ["zh-CN", "最新优先", "最早优先"],
    ["en", "Latest first", "Earliest first"],
    ["ja", "新しい順", "古い順"],
  ] as const) {
    await page.evaluate(
      (value) => localStorage.setItem("tradingagents-locale", value),
      locale,
    );
    await page.goto("/runs/run-report?view=timeline");
    const latest = page.getByRole("button", { name: latestLabel });
    const earliest = page.getByRole("button", { name: earliestLabel });
    await expect(latest).toBeVisible();
    await expect(earliest).toBeVisible();
    for (const control of [latest, earliest]) {
      const box = await control.boundingBox();
      expect(box).not.toBeNull();
      expect(box?.x ?? -1).toBeGreaterThanOrEqual(0);
      expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(390);
      expect(box?.y ?? -1).toBeGreaterThanOrEqual(0);
      expect((box?.y ?? 0) + (box?.height ?? 0)).toBeLessThanOrEqual(844);
    }
    await earliest.click();
    await expect(earliest).toHaveAttribute("aria-pressed", "true");
    await latest.click();
    await expect(latest).toHaveAttribute("aria-pressed", "true");
  }
});

test("compares active and explicitly shown Trash nodes without creating research", async ({
  page,
}) => {
  let comparisonPayload: Record<string, unknown> | null = null;
  let researchCreateCalls = 0;
  const full = {
    id: "comparison-full", cycle_id: "comparison-full", instrument: "NVDA",
    analysis_date: "2026-07-20", research_schema_version: "1",
    information_cutoff_at: "2026-07-20T23:59:59Z",
    method_snapshot: { llm_provider: "fixture-a" }, research_kind: "full",
    full_baseline_run_id: null, is_baseline_compatible: true,
    is_cycle_head: true, is_primary: true, is_active: true, trashed_at: null,
  };
  const incremental = {
    id: "comparison-incremental", cycle_id: "comparison-full", instrument: "NVDA",
    analysis_date: "2026-07-24", research_schema_version: "1",
    information_cutoff_at: "2026-07-24T23:59:59Z",
    method_snapshot: { llm_provider: "fixture-b" }, research_kind: "incremental",
    full_baseline_run_id: "comparison-full", is_baseline_compatible: false,
    is_cycle_head: false, is_primary: true, is_active: false,
    trashed_at: "2026-07-25T00:00:00Z",
  };

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/timelines/NVDA" && request.method() === "GET") {
      const nodes = url.searchParams.get("trash_state") === "all"
        ? [full, incremental]
        : [full];
      return route.fulfill({
        json: cycleTimeline("NVDA", nodes as TimelineNodeFixture[], full.id),
      });
    }
    if (url.pathname === "/api/v1/timelines/NVDA/compare") {
      comparisonPayload = request.postDataJSON() as Record<string, unknown>;
      return route.fulfill({ json: {
        instrument: "NVDA", cross_cycle: false, method_changed: true,
        warnings: [{ code: "method_changed", message: "No automatic attribution." }],
        sides: [
          { node_id: full.id, cycle_id: full.cycle_id, analysis_date: full.analysis_date,
            research_schema_version: "1", method_snapshot: full.method_snapshot,
            research_kind: "full", lifecycle_state: "active", decision: { rating: "hold" } },
          { node_id: incremental.id, cycle_id: incremental.cycle_id,
            analysis_date: incremental.analysis_date, research_schema_version: "1",
            method_snapshot: incremental.method_snapshot, research_kind: "incremental",
            lifecycle_state: "trashed", collection_summary: { version: "1", domains: [] },
            research_availability: { version: "1", domains: [] }, reassessment: { entries: [] },
            full_research_required_reasons: [{ code: "attribution.unreliable",
              message: "Comparison side needs Full research.", origin: "semantic",
              evidence_refs: [] }],
            decision: { rating: "bullish" }, performance: {
              stock: { status: "unavailable", reason: "fixture" }, benchmarks: [],
            } },
        ],
        decision_sections: [{ key: "rating", values: [
          { state: "recorded", value: "hold" },
          { state: "recorded", value: "bullish" },
        ] }],
      } });
    }
    if (url.pathname === "/api/v1/runs" && request.method() === "POST") {
      researchCreateCalls += 1;
    }
    return route.fulfill({ status: 404, json: { detail: "not mocked" } });
  });

  await page.goto("/timelines/NVDA");
  await page.getByRole("button", { name: /Select for comparison|选择用于对照|比較対象に選択/ }).click();
  await page.getByRole("button", { name: /Show retained Trash|显示回收站保留项|ゴミ箱の保持項目を表示/ }).click();
  await expect(page.getByText(/Retained in Trash|保留在回收站|ゴミ箱に保持中/)).toBeVisible();
  await page.getByRole("button", { name: /Select for comparison|选择用于对照|比較対象に選択/ }).click();
  await page.getByRole("button", { name: /Compare selected nodes|对照所选节点|選択したノードを比較/ }).click();

  const comparisonDialog = page.getByRole("dialog", {
    name: /Node Comparison|节点对照|ノード比較/,
  });
  await expect(comparisonDialog).toBeVisible();
  await expect(page.getByText(/Method Changed|方法已变更|メソッド変更/)).toBeVisible();
  await expect(
    page.getByText("Comparison side needs Full research.", { exact: true }),
  ).toBeVisible();
  expect(comparisonPayload).toEqual({ nodes: [
    { node_id: full.id, lifecycle_state: "active" },
    { node_id: incremental.id, lifecycle_state: "trashed" },
  ] });
  expect(researchCreateCalls).toBe(0);

  await comparisonDialog.getByText(/Extended conclusions|扩展结论|拡張結論/).click();
  await comparisonDialog.getByText(/Update audit|更新审计|更新監査/).click();
  await comparisonDialog.getByText(/Raw audit|原始审计|生監査情報/).click();

  await page.setViewportSize({ width: 390, height: 844 });
  const dialogBox = await comparisonDialog.boundingBox();
  expect(dialogBox).not.toBeNull();
  expect(dialogBox?.x).toBe(0);
  expect(dialogBox?.y).toBe(0);
  expect(dialogBox?.width).toBe(390);
  expect(dialogBox?.height).toBe(844);
  const comparisonValues = comparisonDialog.locator(
    ".comparison-decision-table tbody tr",
  ).first().locator("td");
  const leftValueBox = await comparisonValues.nth(0).boundingBox();
  const rightValueBox = await comparisonValues.nth(1).boundingBox();
  expect(leftValueBox).not.toBeNull();
  expect(rightValueBox).not.toBeNull();
  expect(rightValueBox!.y).toBeGreaterThanOrEqual(
    leftValueBox!.y + leftValueBox!.height,
  );
  const comparisonScroll = comparisonDialog.locator(".comparison-modal-scroll");
  await comparisonScroll.focus();
  await page.keyboard.press("PageDown");
  await expect.poll(() => comparisonScroll.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  await comparisonScroll.evaluate((element) => {
    element.scrollTop = 0;
  });
  await comparisonScroll.hover();
  await page.mouse.wheel(0, 400);
  await expect.poll(() => comparisonScroll.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  await expect.poll(() => page.evaluate(() => document.body.style.overflow)).toBe("hidden");
});

test("covers every supported retained-node comparison pair", async ({ page }) => {
  const makeNode = (
    id: string,
    researchKind: "full" | "incremental",
    cycleId: string,
  ) => ({
    id, cycle_id: cycleId, instrument: "NVDA",
    analysis_date: id === "full-a" ? "2026-07-20" : id === "full-b" ? "2026-07-21" : id === "incremental-a" ? "2026-07-24" : "2026-07-25",
    research_schema_version: "1", information_cutoff_at: "2026-07-24T23:59:59Z",
    method_snapshot: {}, research_kind: researchKind,
    full_baseline_run_id: researchKind === "full" ? null : cycleId,
    is_baseline_compatible: researchKind === "full", is_cycle_head: true,
    is_primary: cycleId === "cycle-a", is_active: true, trashed_at: null,
  });
  const pairs = [
    [makeNode("full-a", "full", "cycle-a"), makeNode("full-b", "full", "cycle-b")],
    [makeNode("full-a", "full", "cycle-a"), makeNode("incremental-a", "incremental", "cycle-a")],
    [makeNode("incremental-a", "incremental", "cycle-a"), makeNode("incremental-b", "incremental", "cycle-a")],
    [makeNode("incremental-a", "incremental", "cycle-a"), makeNode("incremental-b", "incremental", "cycle-b")],
  ];
  let currentPair = pairs[0];
  const comparisonPayloads: Record<string, unknown>[] = [];

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/timelines/NVDA" && request.method() === "GET") {
      return route.fulfill({
        json: cycleTimeline(
          "NVDA",
          currentPair as TimelineNodeFixture[],
          "cycle-a",
        ),
      });
    }
    if (url.pathname === "/api/v1/timelines/NVDA/compare") {
      comparisonPayloads.push(request.postDataJSON() as Record<string, unknown>);
      const crossCycle = currentPair[0].cycle_id !== currentPair[1].cycle_id;
      return route.fulfill({ json: {
        instrument: "NVDA", cross_cycle: crossCycle, method_changed: false,
        warnings: [], sides: currentPair.map((node) => ({
          node_id: node.id, cycle_id: node.cycle_id,
          analysis_date: node.analysis_date, research_schema_version: "1",
          method_snapshot: {}, research_kind: node.research_kind,
          lifecycle_state: "active", decision: { rating: "hold" },
        })), decision_sections: [],
      } });
    }
    return route.fulfill({ status: 404, json: { detail: "not mocked" } });
  });

  for (const [index, pair] of pairs.entries()) {
    currentPair = pair;
    await page.goto(`/timelines/NVDA?pair=${index}`);
    for (const node of pair) {
      const card = page
        .locator(`.research-node-card.${node.research_kind}`)
        .filter({ hasText: node.analysis_date })
        .first();
      await card.getByRole("button", {
        name: /Select for comparison|选择用于对照|比較対象に選択/,
      }).click();
    }
    await page.getByRole("button", {
      name: /Compare selected nodes|对照所选节点|選択したノードを比較/,
    }).click();
    await expect(page.getByRole("dialog", {
      name: /Node Comparison|节点对照|ノード比較/,
    })).toBeVisible();
    expect(comparisonPayloads.at(-1)).toEqual({ nodes: pair.map((node) => ({
      node_id: node.id, lifecycle_state: "active",
    })) });
  }
});

test("enforces selection cardinality and surfaces every comparison rejection", async ({
  page,
}) => {
  const nodes = ["full-a", "incremental-a", "incremental-b"].map((id, index) => ({
    id, cycle_id: "full-a", instrument: "NVDA", analysis_date: "2026-07-24",
    research_schema_version: "1", information_cutoff_at: "2026-07-24T23:59:59Z",
    method_snapshot: {}, research_kind: index === 0 ? "full" : "incremental",
    full_baseline_run_id: index === 0 ? null : "full-a",
    is_baseline_compatible: index === 0, is_cycle_head: index === 2,
    is_primary: true, is_active: true, trashed_at: null,
  }));
  const rejectionMessages = [
    "Legacy-only nodes are not comparable",
    "Failed or cancelled nodes are not comparable",
    "Selected nodes must belong to one Instrument",
    "Research Node was not found",
    "Purged Research Nodes cannot be compared",
    "Trash lifecycle state must be explicit",
  ];
  let rejectionMessage = rejectionMessages[0];

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (url.pathname === "/api/v1/timelines/NVDA" && request.method() === "GET") {
      return route.fulfill({
        json: cycleTimeline(
          "NVDA",
          nodes as TimelineNodeFixture[],
          "full-a",
        ),
      });
    }
    if (url.pathname === "/api/v1/timelines/NVDA/compare") {
      return route.fulfill({ status: 422, json: { error: {
        code: "invalid_research_node_comparison", message: rejectionMessage,
      } } });
    }
    return route.fulfill({ status: 404, json: { detail: "not mocked" } });
  });

  for (const [index, message] of rejectionMessages.entries()) {
    rejectionMessage = message;
    await page.goto(`/timelines/NVDA?rejection=${index}`);
    const compareButton = page.getByRole("button", {
      name: /Compare selected nodes|对照所选节点|選択したノードを比較/,
    });
    const selectButtons = page.getByRole("button", {
      name: /Select for comparison|选择用于对照|比較対象に選択/,
    });
    await expect(compareButton).toBeDisabled();
    await selectButtons.nth(0).click();
    await expect(compareButton).toBeDisabled();
    await selectButtons.nth(0).click();
    await expect(compareButton).toBeEnabled();
    await expect(selectButtons.nth(0)).toBeDisabled();
    await compareButton.click();
    await expect(page.getByText(message)).toBeVisible();
    await expect(page.getByRole("dialog", {
      name: /Node Comparison|节点对照|ノード比較/,
    })).toBeHidden();
  }
});

test("completes a mocked Full-to-Incremental Timeline journey", async ({ page }) => {
  let stage: "none" | "full" | "incremental" = "none";
  let incrementalPayload: Record<string, unknown> | null = null;

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
        { id: "gpt-5.4-mini", label: "GPT quick", compatibility: "supported",
          reasoning_efforts: ["provider_default"], default_roles: ["quick"] },
        { id: "gpt-5.5", label: "GPT deep", compatibility: "supported",
          reasoning_efforts: ["provider_default"], default_roles: ["deep"] },
      ], source: "fixture", fetched_at: timestamp, stale: false, warning: null } });
    }
    if (path === "/api/v1/instruments/recent") return route.fulfill({ json: [] });
    if (/^\/api\/v1\/instruments\/[^/]+\/analysis-cutoff-context$/.test(path)) {
      return route.fulfill({ json: {
        instrument: "NVDA", market_timezone: "America/New_York",
        market_date: "2026-07-24", max_analysis_date: "2026-07-24",
        observed_at: timestamp, valid_until: "2999-01-01T00:00:00Z",
      } });
    }
    if (path === "/api/v1/timelines/NVDA/baseline-candidates") {
      return route.fulfill({ json: {
        instrument: "NVDA",
        before: url.searchParams.get("before"),
        items: stage === "none" ? [] : [{
          id: "full-journey",
          analysis_date: "2026-07-20",
          is_primary: true,
          rating: "Hold",
          confidence: 0.65,
          instrument_name: "NVIDIA Corporation",
          instrument_local_name: null,
          thesis: "Evidence is balanced.",
          cycle_warning: false,
        }],
      } });
    }
    if (path === "/api/v1/runs" && request.method() === "POST") {
      const payload = request.postDataJSON() as Record<string, unknown>;
      const isIncremental = payload.research_kind === "incremental";
      stage = isIncremental ? "incremental" : "full";
      incrementalPayload = isIncremental ? payload : incrementalPayload;
      const id = isIncremental ? "incremental-journey" : "full-journey";
      const run = makeRun(id, "succeeded", { ticker: "NVDA" });
      run.request = { ...run.request, ...payload };
      return route.fulfill({ status: 202, json: run });
    }
    if (path === "/api/v1/timelines/NVDA") {
      const full = {
        id: "full-journey", cycle_id: "full-journey", instrument: "NVDA",
        analysis_date: "2026-07-20", research_schema_version: "1",
        information_cutoff_at: "2026-07-20T23:59:59Z",
        method_snapshot: { llm_provider: "fixture" }, research_kind: "full",
        full_baseline_run_id: null, is_baseline_compatible: true,
        is_cycle_head: stage !== "incremental", is_primary: true, is_active: true,
        trashed_at: null, collection_summary: null, research_availability: null,
        reassessment: null, decision: result("full-journey").decision,
        performance: null, cycle_warning: stage === "incremental",
        full_research_required_reasons: [],
      };
      const incremental = {
        id: "incremental-journey", cycle_id: "full-journey", instrument: "NVDA",
        analysis_date: "2026-07-24", research_schema_version: "1",
        information_cutoff_at: timestamp, method_snapshot: { llm_provider: "fixture" },
        research_kind: "incremental", full_baseline_run_id: "full-journey",
        is_baseline_compatible: false, is_cycle_head: true, is_primary: true,
        is_active: true, trashed_at: null,
        collection_summary: { version: "1", market: "united_states", domains: [{
          domain: "news", state: "empty", sources: [{
            source: "fixture", fallback: false, retrieved_at: timestamp,
          }], temporal_bases: [], evidence_refs: [],
        }] },
        research_availability: { version: "1", domains: [{ domain: "news", status: "missing" }] },
        reassessment: { entries: [{ component_id: "thesis", disposition: "reaffirmed",
          reason: "The bounded update did not change the thesis.", evidence_refs: [] }] },
        decision: { rating: "Hold", thesis: "Current complete decision" },
        performance: { stock: { status: "not_yet_observable",
          reason: "No completed interval." }, benchmarks: [] },
        cycle_warning: true,
        full_research_required_reasons: [{ code: "attribution.unresolved",
          message: "The bounded update cannot resolve attribution.", origin: "semantic",
          evidence_refs: [] }],
      };
      const nodes = stage === "none" ? [] :
        stage === "full" ? [full] : [full, incremental];
      return route.fulfill({ json: cycleTimeline(
        "NVDA",
        nodes as TimelineNodeFixture[],
        stage === "none" ? null : "full-journey",
        stage === "incremental",
      ) });
    }
    const runMatch = path.match(/^\/api\/v1\/runs\/([^/]+)$/);
    if (runMatch) {
      const id = runMatch[1];
      const isIncremental = id === "incremental-journey";
      const run = makeRun(id, "succeeded", { ticker: "NVDA" });
      run.request = { ...run.request, research_kind: isIncremental ? "incremental" : "full",
        full_baseline_run_id: isIncremental ? "full-journey" : null };
      return route.fulfill({ json: { run, result: result(id), attempts: [],
        evidence_status: { status: "sealed", digest: "fixture-digest", item_count: 1,
          table_count: 0, sealed_attempt: 1, sealed_at: timestamp } } });
    }
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });

  await page.goto("/runs/new");
  await page.locator("#new-run-ticker").fill("NVDA");
  await page.locator("#new-run-analysis-date").fill("2026-07-20");
  await page.locator("form.run-form button").last().click();
  await expect(page).toHaveURL(/\/runs\/full-journey$/);

  await page.goto("/runs/new");
  await page.locator("#new-run-ticker").fill("NVDA");
  await page.locator("#new-run-analysis-date").fill("2026-07-24");
  await expect(page.locator('input[name="research-kind"]').nth(1)).toBeEnabled();
  await page.locator('input[name="research-kind"]').nth(1).check();
  await page.locator("form.run-form select").first().selectOption("full-journey");
  await page.locator("form.run-form button").last().click();
  await expect(page).toHaveURL(/\/runs\/incremental-journey$/);
  expect(incrementalPayload).toMatchObject({ research_kind: "incremental",
    full_baseline_run_id: "full-journey" });

  await page.goto("/timelines/NVDA");
  await expect(page.locator(".research-node-card.full")).toBeVisible();
  await expect(page.locator(".research-node-card.incremental")).toBeVisible();
  await page.getByText(/Update details|更新详情|更新詳細/).click();
  await expect(
    page.getByText(/Research Availability|研究可用性|リサーチ可用性/),
  ).toBeVisible();
  await expect(page.getByText("Current complete decision")).toBeVisible();
  await expect(page.getByText("The bounded update cannot resolve attribution.")).toBeVisible();
  await expect(
    page.getByText(/Full research recommended|建议进行完整研究/).first(),
  ).toBeVisible();
  const warningGeometry = await page
    .locator(".research-node-card.incremental .research-warning-block")
    .evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        borderRadius: Number.parseFloat(style.borderRadius),
        borderWidth: Number.parseFloat(style.borderTopWidth),
        paddingLeft: Number.parseFloat(style.paddingLeft),
        paddingRight: Number.parseFloat(style.paddingRight),
      };
    });
  expect(warningGeometry.borderRadius).toBeGreaterThanOrEqual(9);
  expect(warningGeometry.borderWidth).toBeGreaterThanOrEqual(1);
  expect(warningGeometry.paddingLeft).toBeGreaterThanOrEqual(14);
  expect(warningGeometry.paddingRight).toBeGreaterThanOrEqual(14);
});
