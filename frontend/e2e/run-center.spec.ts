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
      calculation_records: [],
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

function review(runId = "legacy-run") {
  return {
    outcome_id: 7,
    review_status: "feedback_available",
    lifecycle_actions_allowed: true,
    run_id: runId,
    ticker: "7203.T",
    instrument_name: "Toyota Motor Corporation",
    instrument_local_name: "トヨタ自動車",
    market: "Asia/Tokyo",
    asset_type: "stock",
    analysis_date: "2026-07-24",
    profile: "standard",
    decision: result(runId).decision,
    outcome: {
      status: "resolved",
      source_decision_id: 1,
      source_revision_id: null,
      benchmark: "1321.T",
      market_timezone: "Asia/Tokyo",
      method_category: "short_term_relative_return",
      method_version: "short_term_relative_return.v1",
      price_semantics: "exchange_local_daily_close",
      adjustment_semantics: "split_and_dividend_adjusted",
      horizon_limit: "Five common trading intervals are short-term methodological feedback only.",
      limitations: [],
      observation_start: "2026-07-25",
      observation_end: "2026-08-01",
      holding_intervals: 5,
      raw_return: 0.08,
      alpha_return: 0.03,
      data_available_at: timestamp,
      last_checked_at: timestamp,
      next_check_at: null,
      error_message: null,
    },
    reflection: "The evidence was directionally useful.",
    method_feedback: "The evidence was directionally useful.",
    outcome_reflection: {
      status: "generated",
      created_at: timestamp,
      generated_at: timestamp,
      last_attempted_at: timestamp,
      next_retry_at: null,
      error_code: null,
      generation_cycle: null,
    },
    outcome_feedback: {
      id: 17,
      status: "eligible",
      qualification_policy_version: "outcome_feedback_qualification.v1",
      reasons: [],
      method_category: "short_term_relative_return",
      horizon_limit: "Five common trading intervals are short-term methodological feedback only.",
      applicability: {
        schema_version: "1",
        scope: "instrument",
        instrument: "7203.T",
        market: "Asia/Tokyo",
        research_stages: [],
        research_domains: [],
        method_category: "short_term_relative_return",
        horizon: "short_term",
      },
      qualified_at: timestamp,
      available_at: timestamp,
      retirement_reason: null,
      retirement_note: null,
      retired_at: null,
    },
  };
}

test("updates the Primary Research Chain through a Full-only Indeterminate head", async ({
  page,
}) => {
  await page.addInitScript(() => {
    window.localStorage.setItem("tradingagents-locale", "en");
  });
  let updatePayload: Record<string, unknown> | null = null;
  const revision = {
    id: "revision-2",
    chain_id: "chain-1",
    sequence: 2,
    predecessor_revision_id: "revision-1",
    producing_run_id: "run-full",
    cutoff: "2026-07-24",
    role: "update",
    execution_strategy: "full",
    change_conclusion: "indeterminate",
    indeterminate_reason: "coverage_incomplete",
    delta: { claims: [], questions: [], change_signals: [] },
    current_state: {
      instrument: "6501.T",
      cutoff: "2026-07-24",
      language: "en",
      opinion: { rating: "Hold", confidence: "medium", thesis: "Demand remains uncertain." },
      claims: [],
      questions: [],
      scenarios: [],
      risks: [],
      catalysts: [],
      invalidation_conditions: [],
    },
    coverage: {
      supports_no_material_change: false,
      limitations: ["TDnet archive coverage is incomplete."],
      domains: [],
      claims: [],
      questions: [],
    },
    update_summary: { summary: "Full Analysis remained inconclusive." },
    evidence_snapshot: {
      bundle: { items: [] },
      lineage: [],
      source_records: [],
      source_record_lineage: [],
      source_watermarks: [],
    },
    research_update_audit: null,
    metrics: {},
    created_at: timestamp,
  };
  const chain = {
    id: "chain-1",
    instrument: "6501.T",
    is_primary: true,
    current_revision_id: revision.id,
    current_revision: revision,
    revisions: [revision],
    next_update_policy: "full_required",
    next_update_reason: "indeterminate_head",
    created_at: timestamp,
    updated_at: timestamp,
  };

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    if (request.method() === "GET" && request.url().endsWith("/research-chains/chain-1")) {
      await route.fulfill({ json: chain });
      return;
    }
    if (request.method() === "POST" && request.url().endsWith("/research-chains/chain-1/updates")) {
      updatePayload = request.postDataJSON() as Record<string, unknown>;
      await route.fulfill({ status: 202, json: makeRun("run-reassessment", "queued") });
      return;
    }
    await route.fulfill({ status: 404, json: {} });
  });

  await page.goto("/research/chain-1");
  await expect(page.getByRole("alert")).toContainText("Indeterminate");
  await expect(page.getByRole("alert")).toContainText("TDnet archive coverage is incomplete.");
  await page.getByRole("button", { name: "Run Full reassessment" }).click();
  await expect.poll(() => updatePayload).toMatchObject({
    baseline_revision_id: "revision-2",
    analysis_date: "2026-07-25",
    execution_strategy: "full",
  });
});

test("keeps retained Review references, actions, and motion preferences usable in Chromium", async ({
  page,
}) => {
  await page.addInitScript(() => {
    window.localStorage.setItem("tradingagents-locale", "en");
    const key = "__reviewScrollCalls";
    const calls = (window as unknown as Record<string, Array<{ id: string; behavior: string }>>)[key] = [];
    const original = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = function scrollIntoView(options) {
      calls.push({
        id: this.id,
        behavior: typeof options === "object" && options?.behavior
          ? options.behavior
          : "auto",
      });
      original.call(this, options);
    };
  });
  await page.emulateMedia({ reducedMotion: "reduce" });

  const sourceRun = makeRun("review-source", "succeeded", {
    ticker: "7203.T",
    instrumentName: "Toyota Motor Corporation",
  });
  const sourceResult = {
    ...result(sourceRun.id),
    decision: {
      ...result(sourceRun.id).decision,
      memory_refs: ["memory:legacy-run"],
    },
  };
  const sourceDetail = {
    run: sourceRun,
    result: sourceResult,
    attempts: [],
    evidence_status: {
      status: "sealed",
      digest: sourceResult.evidence.digest,
      item_count: sourceResult.evidence.items.length,
      table_count: sourceResult.evidence.tables.length,
      sealed_attempt: 1,
      sealed_at: timestamp,
    },
  };
  const baseReview = review();
  const failedReview = {
    ...baseReview,
    review_status: "reflection_failed",
    reflection: null,
    method_feedback: null,
    outcome_reflection: {
      ...baseReview.outcome_reflection,
      status: "retryable_failure",
      error_code: "provider_timeout",
    },
    outcome_feedback: null,
  };
  const reviewQueries: string[] = [];
  const paletteStatuses = [
    "awaiting_observation",
    "awaiting_reflection",
    "observation_delayed",
    "reflection_retry_scheduled",
    "feedback_ineligible",
    "reflection_failed",
    "reflection_invalid",
    "lifecycle_inconsistent",
    "feedback_available",
    "feedback_retired",
  ] as const;
  let feedbackRetired = false;
  let reflectionRegenerationRequested = false;
  let retirementPayload: Record<string, unknown> | null = null;
  const retiredReview = () => ({
    ...baseReview,
    review_status: "feedback_retired",
    method_feedback: null,
    outcome_feedback: {
      ...baseReview.outcome_feedback,
      status: "retired",
      retirement_reason: "too_specific",
      retirement_note: null,
      retired_at: timestamp,
    },
  });

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path === "/api/v1/instruments/recent") {
      return route.fulfill({ json: [] });
    }
    if (path === "/api/v1/runs/review-source") {
      return route.fulfill({ json: sourceDetail });
    }
    if (path === "/api/v1/runs/review-source/artifacts") {
      return route.fulfill({ json: [] });
    }
    if (path === "/api/v1/runs/review-source/evidence") {
      return route.fulfill({ json: sourceResult.evidence });
    }
    if (path === "/api/v1/runs/review-source/events") {
      return route.fulfill({
        headers: { "Content-Type": "text/event-stream" },
        body: "",
      });
    }
    if (path === "/api/v1/reviews") {
      reviewQueries.push(url.search);
      if (url.searchParams.get("q") === "status-palette") {
        return route.fulfill({
          json: paletteStatuses.map((status, index) => ({
            ...baseReview,
            outcome_id: 100 + index,
            run_id: `palette-${status}`,
            review_status: status,
            lifecycle_actions_allowed: false,
          })),
        });
      }
      let currentReview = baseReview;
      if (url.searchParams.get("status_group") === "needs_attention") {
        currentReview = failedReview;
      } else if (feedbackRetired) {
        currentReview = retiredReview();
      }
      return route.fulfill({ json: [currentReview] });
    }
    if (path === "/api/v1/reviews/7") {
      return route.fulfill({
        json: {
          review: feedbackRetired ? retiredReview() : baseReview,
          reflection: [
            "Directional assessment: mixed",
            "Source-decision evidence lesson: The source decision left an evidence gap.",
            "Method lesson",
            "Separate absolute price performance from relative alpha.",
          ].join("\n"),
          attempts: [],
          aggregate_usage: {
            usage_status: "reported",
            attempt_count: 1,
            llm_calls: 1,
            input_tokens: 120,
            output_tokens: 40,
            cache_hit_input_tokens: 20,
            cache_miss_input_tokens: 100,
            reasoning_output_tokens: 12,
            wall_time_seconds: 1.4,
            provider_reported_cost_usd: 0.004,
          },
        },
      });
    }
    if (
      path === "/api/v1/outcome-observations/7/reflection-regenerations" &&
      request.method() === "POST"
    ) {
      reflectionRegenerationRequested = true;
      return route.fulfill({
        json: {
          cycle: {
            id: "cycle-manual",
            outcome_id: 7,
            status: "queued",
            origin: "manual",
            trigger: "user_regeneration",
            retry_ordinal: 0,
            queued_at: timestamp,
            due_at: timestamp,
          },
          review_status: "awaiting_reflection",
          reflection_status: "pending",
        },
      });
    }
    if (path === "/api/v1/outcome-feedback/17/retire" && request.method() === "POST") {
      retirementPayload = request.postDataJSON() as Record<string, unknown>;
      feedbackRetired = true;
      return route.fulfill({
        json: {
          status: "retired",
          review_status: "feedback_retired",
          retirement_reason: "too_specific",
          retirement_note: null,
          retired_at: timestamp,
        },
      });
    }
    return route.fulfill({ status: 404, json: { detail: "Not found" } });
  });

  await page.goto("/runs/review-source?view=decision");
  const historicalReference = page.getByRole("link", {
    name: "Open Research Review memory:legacy-run",
  });
  await expect(historicalReference).toHaveAttribute(
    "href",
    "/reviews?q=legacy-run#review-legacy-run",
  );
  await historicalReference.click();
  await expect(page).toHaveURL(/\/reviews\?q=legacy-run#review-legacy-run$/);
  await expect(page.locator("#review-legacy-run")).toBeFocused();
  await expect.poll(async () => page.evaluate(() => (
    (window as unknown as Record<string, Array<{ id: string; behavior: string }>>)
      .__reviewScrollCalls
      .find((call) => call.id === "review-legacy-run")?.behavior
  ))).toBe("auto");

  await page.setViewportSize({ width: 1280, height: 900 });
  const reviewCard = page.locator("#review-legacy-run");
  await expect(reviewCard.locator(".memory-profile")).toHaveCSS(
    "background-color",
    "rgb(237, 243, 255)",
  );
  await expect(reviewCard.locator(".ticker")).toHaveCSS("font-size", "18px");
  await expect(reviewCard.locator(".instrument-primary-name")).toHaveText(
    "トヨタ自動車",
  );
  await expect(reviewCard.locator(".instrument-primary-name")).toHaveCSS(
    "font-size",
    "14px",
  );
  await expect(reviewCard.locator(".instrument-secondary-name")).toHaveText(
    "Toyota Motor Corporation",
  );
  const [reviewTickerBox, reviewPrimaryNameBox, reviewSecondaryNameBox] =
    await Promise.all([
      reviewCard.locator(".ticker").boundingBox(),
      reviewCard.locator(".instrument-primary-name").boundingBox(),
      reviewCard.locator(".instrument-secondary-name").boundingBox(),
    ]);
  expect(reviewTickerBox).not.toBeNull();
  expect(reviewPrimaryNameBox).not.toBeNull();
  expect(reviewSecondaryNameBox).not.toBeNull();
  expect(reviewPrimaryNameBox!.x).toBeGreaterThan(reviewTickerBox!.x + reviewTickerBox!.width);
  expect(reviewSecondaryNameBox!.x).toBe(reviewPrimaryNameBox!.x);
  expect(reviewSecondaryNameBox!.y).toBeGreaterThan(reviewPrimaryNameBox!.y);
  await expect(reviewCard.locator(".status-feedback_available")).toHaveCSS(
    "background-color",
    "rgb(234, 248, 240)",
  );
  expect(await reviewCard.locator(".review-confidence").evaluate(
    (element) => element.scrollWidth <= element.clientWidth,
  )).toBe(true);
  const [ratingBox, confidenceBox, thesisBox] = await Promise.all([
    reviewCard.locator(".review-rating").boundingBox(),
    reviewCard.locator(".review-confidence").boundingBox(),
    reviewCard.locator(".memory-decision > .markdown").boundingBox(),
  ]);
  expect(ratingBox).not.toBeNull();
  expect(confidenceBox).not.toBeNull();
  expect(thesisBox).not.toBeNull();
  expect(confidenceBox!.y).toBeGreaterThan(ratingBox!.y + ratingBox!.height);
  expect(thesisBox!.x).toBeGreaterThan(ratingBox!.x + ratingBox!.width);
  await expect(reviewCard.getByRole("heading", { name: "Source Research Decision" })).toHaveCSS(
    "font-size",
    "16px",
  );
  await reviewCard.getByText("Decision details").click();
  const detailsWidth = await reviewCard.locator(".memory-decision-details").first().evaluate(
    (element) => element.getBoundingClientRect().width,
  );
  const cardWidth = await reviewCard.evaluate((element) => element.getBoundingClientRect().width);
  expect(detailsWidth / cardWidth).toBeGreaterThan(0.9);
  expect(await reviewCard.locator(".memory-scenario-grid").evaluate(
    (element) => getComputedStyle(element).gridTemplateColumns.split(" ").length,
  )).toBe(3);
  await reviewCard.getByText("Full Reflection Analysis").click();
  await expect(reviewCard.getByText("Directional assessment")).toBeVisible();
  await expect(reviewCard.getByText("Mixed")).toBeVisible();
  await expect(reviewCard.getByText("Source-decision evidence lesson")).toBeVisible();
  await expect(reviewCard.getByText("Method lesson", { exact: true })).toBeVisible();
  await reviewCard.getByText("Generation and audit details").click();
  await expect(reviewCard.getByRole("heading", { name: "Usage summary" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const [mobileMetaBox, mobileThesisBox] = await Promise.all([
    reviewCard.locator(".review-decision-meta").boundingBox(),
    reviewCard.locator(".memory-decision > .markdown").boundingBox(),
  ]);
  expect(mobileMetaBox).not.toBeNull();
  expect(mobileThesisBox).not.toBeNull();
  expect(mobileThesisBox!.y).toBeGreaterThan(mobileMetaBox!.y + mobileMetaBox!.height);
  expect(await reviewCard.locator(".memory-scenario-grid").evaluate(
    (element) => getComputedStyle(element).gridTemplateColumns.split(" ").length,
  )).toBe(1);
  await page.setViewportSize({ width: 1280, height: 900 });

  await page.getByLabel("Keyword search").fill("Toyota");
  await page.getByLabel("Review status").selectOption("needs_attention");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page).toHaveURL(/\/reviews\?q=Toyota&status_group=needs_attention$/);
  await expect.poll(() => reviewQueries).toContain("?q=Toyota&status_group=needs_attention");
  const regenerationButton = page.getByRole("button", { name: "Regenerate Reflection Analysis" });
  expect(await regenerationButton.evaluate((element) => element.getBoundingClientRect().width)).toBeLessThan(cardWidth / 2);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await regenerationButton.evaluate((element) => {
    const section = element.closest("section")!;
    return Math.abs(element.getBoundingClientRect().width - section.getBoundingClientRect().width) < 2;
  })).toBe(true);
  expect(await page.locator(".memory-scenario-grid").evaluate(
    (element) => getComputedStyle(element).gridTemplateColumns.split(" ").length,
  )).toBe(1);
  await regenerationButton.click();
  await expect.poll(() => reflectionRegenerationRequested).toBe(true);
  await expect(page.getByRole("status")).toContainText("Reflection Analysis is queued.");
  await expect(page.getByRole("button", { name: "Queued" })).toBeDisabled();
  await expect(page.locator("#review-legacy-run")).toBeFocused();

  await page.getByLabel("Review status").selectOption("feedback_available");
  await page.getByRole("button", { name: "Apply" }).click();
  await expect(page).toHaveURL(/\/reviews\?q=Toyota&status_group=feedback_available$/);

  const retireTrigger = page.getByRole("button", { name: "Retire Method Lesson" });
  await expect(retireTrigger).toHaveCSS("min-height", "44px");
  expect(await retireTrigger.evaluate((element) => element.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);

  await retireTrigger.focus();
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Retire Reusable Method Lesson" });
  const reason = dialog.getByLabel("Reason");
  await expect(reason).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(dialog.getByRole("button", { name: "Retire Method Lesson" })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(reason).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  await expect(retireTrigger).toBeFocused();

  await page.keyboard.press("Enter");
  await expect(reason).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  await page.keyboard.press("Tab");
  await expect(dialog.getByRole("button", { name: "Retire Method Lesson" })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect.poll(() => retirementPayload).toEqual({ reason: "too_specific", note: null });
  await expect(page.getByRole("status")).toHaveText("Reusable method lesson retired.");

  await page.goto("/reviews?q=status-palette");
  const toneBackgrounds = {
    awaiting_observation: "rgb(237, 243, 255)",
    awaiting_reflection: "rgb(237, 243, 255)",
    observation_delayed: "rgb(255, 245, 223)",
    reflection_retry_scheduled: "rgb(255, 245, 223)",
    feedback_ineligible: "rgb(255, 245, 223)",
    reflection_failed: "rgb(255, 240, 242)",
    reflection_invalid: "rgb(255, 240, 242)",
    lifecycle_inconsistent: "rgb(255, 240, 242)",
    feedback_available: "rgb(234, 248, 240)",
    feedback_retired: "rgb(241, 243, 246)",
  } as const;
  for (const [status, background] of Object.entries(toneBackgrounds)) {
    await expect(page.locator(`.status-${status}`)).toHaveCSS("background-color", background);
  }
});

test("runs, legacy templates, trash, and restores local research", async ({
  page,
}) => {
  test.setTimeout(60_000);
  const runs = new Map<string, MockRun>([
    [
      "run-report",
      makeRun("run-report", "succeeded", {
        ticker: "NVDA",
        instrumentName: "NVIDIA Corporation",
        instrumentLocalName: "英伟达",
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
          instrument_local_name: run.instrument_local_name,
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
    if (path === "/api/v1/reviews") {
      const report = runs.get("run-report");
      if (!report || report.trashed_at || purged.has(report.id)) {
        return route.fulfill({ json: [] });
      }
      return route.fulfill({
        json: [
          {
            outcome_id: 1,
            review_status: "feedback_available",
            lifecycle_actions_allowed: true,
            run_id: report.id,
            ticker: report.request.ticker,
            instrument_name: report.instrument_name,
            instrument_local_name: report.instrument_local_name,
            market: "America/New_York",
            asset_type: "stock",
            analysis_date: "2026-07-24",
            profile: report.request.profile,
            decision: result(report.id).decision,
            outcome: {
              status: "resolved",
              source_decision_id: 1,
              source_revision_id: null,
              benchmark: "SPY",
              market_timezone: "America/New_York",
              method_category: "short_term_relative_return",
              method_version: "short_term_relative_return.v1",
              price_semantics: "exchange_local_daily_close",
              adjustment_semantics: "split_and_dividend_adjusted",
              horizon_limit: "Five common trading intervals are short-term methodological feedback only.",
              limitations: [],
              observation_start: "2026-07-25",
              observation_end: "2026-08-01",
              holding_intervals: 5,
              raw_return: 0.08,
              alpha_return: 0.03,
              data_available_at: timestamp,
              last_checked_at: timestamp,
              next_check_at: null,
              error_message: null,
            },
            reflection: "The evidence was directionally useful.",
            method_feedback: "The evidence was directionally useful.",
            outcome_reflection: null,
            outcome_feedback: null,
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
  const dashboardIdentity = page
    .locator("tbody .instrument-identity")
    .filter({ hasText: "NVDA" });
  await expect(dashboardIdentity.locator(".ticker")).toHaveCSS("font-size", "15px");
  await expect(dashboardIdentity.locator(".instrument-primary-name")).toHaveText("英伟达");
  await expect(dashboardIdentity.locator(".instrument-secondary-name")).toHaveText(
    "NVIDIA Corporation",
  );
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

  await expect(
    page.getByRole("link", { name: "New from this run" }),
  ).toHaveCount(0);
  await page.goto("/runs/new?from_run=run-created");
  await expect(ticker).toHaveValue("7203.T");
  await ticker.fill("MSFT");
  await page.getByRole("button", { name: /Queue research/ }).click();
  await expect(page).toHaveURL(/\/runs\/run-template$/);
  expect(lastCreatePayload).toMatchObject({
    ticker: "MSFT",
    source_run_id: "run-created",
  });

  await page.goto("/runs/run-report?view=deliberation");
  const runIdentity = page.locator(".run-title .instrument-identity");
  await expect(runIdentity.locator(".instrument-primary-name")).toHaveText("英伟达");
  await expect(runIdentity.locator(".instrument-primary-name")).toHaveCSS(
    "font-size",
    "15px",
  );
  await expect(runIdentity.locator(".instrument-secondary-name")).toHaveText(
    "NVIDIA Corporation",
  );
  await expect(runIdentity.locator(".instrument-secondary-name")).toHaveCSS(
    "font-size",
    "13px",
  );
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

  await page.getByRole("link", { name: "Research Review", exact: true }).click();
  await expect(page).toHaveURL(/\/reviews$/);
  await expect(page.getByText("NVIDIA Corporation")).toBeVisible();
  await page.getByText("Decision details").focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("Demand improves").first()).toBeVisible();
  await page
    .getByRole("link", { name: /^Open research decision/ })
    .click();
  await expect(page).toHaveURL(/\/runs\/run-report\?view=decision/);

  await page.goto("/memory");
  await expect(page).toHaveURL(/\/memory$/);
  await expect(page.getByRole("heading", { name: "Research Review" })).toHaveCount(0);

  await page.goto("/runs");
  const reportRow = page.getByRole("row").filter({ hasText: "NVDA" });
  await expect(reportRow.locator(".instrument-primary-name")).toHaveText("英伟达");
  await expect(reportRow.locator(".instrument-secondary-name")).toHaveText(
    "NVIDIA Corporation",
  );
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

  await page.goto("/reviews");
  await expect(page.getByText("No Research Reviews.")).toBeVisible();

  await page.goto("/runs?trash_state=trashed");
  await expect(page.getByText("Trash retention")).toBeVisible();
  const trashedRow = page.getByRole("row").filter({ hasText: "NVDA" });
  await trashedRow.getByRole("checkbox").check();
  await page.getByRole("button", { name: "Restore selected (1)" }).click();
  await expect(page.getByText("Restored 1 run(s).")).toBeVisible();
  await page.goto("/reviews");
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
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await expect(page.locator(".run-title .instrument-primary-name")).toHaveText("英伟达");
  await expect(page.locator(".run-title .instrument-secondary-name")).toHaveText(
    "NVIDIA Corporation",
  );
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
});
