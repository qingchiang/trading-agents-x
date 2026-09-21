import {
act,
fireEvent,
render,
screen,
waitFor,
within,
} from "@testing-library/react";
import { expect,test,vi } from "vitest";
import { Router } from "../../app/router";
import {
api,
type RunDetail as RunDetailType,
type RunEvent
} from "../../shared/api/client";
import i18n from "../../shared/i18n";
import { detail,FakeEventSource,incrementalDetailWithPerformanceReason,LocationProbe,performanceCalculation,ReaderFixture } from "./researchTestFixtures";

test("keeps research readable and exposes technical records only in diagnostics", async () => {
  render(<Router initialPath="/timelines/NVDA?node=run-1"><ReaderFixture /></Router>);
  await screen.findByRole("link", { name: "Overview" });
  expect(screen.getByText("One run-level fixture warning.")).toBeVisible();
  expect(screen.queryByText("Structured recoveries")).not.toBeInTheDocument();
  expect(screen.queryByText("Decision-critical calculation audit")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Export" }));
  expect(screen.getByRole("link", { name: "Export research package" })).toHaveAttribute("href", "/api/v1/runs/run-1/export?format=package");
  fireEvent.click(screen.getByRole("button", { name: "More" }));
  fireEvent.click(screen.getByRole("link", { name: "Run & diagnostics" }));
  await screen.findByRole("heading", { name: "Recovery records" });
  expect(screen.getByText("debate.agenda.serialize", { selector: "code" })).toBeVisible();
  expect(screen.getByText("Decision-critical calculation audit")).toBeVisible();
});

test("keeps a degraded numeric audit compact and opens run warnings on demand", async () => {
  const degraded = structuredClone(detail);
  degraded.result!.decision!.numeric_audit_status = "incomplete";
  degraded.result!.numeric_audit = {
    status: "incomplete",
    omitted_components: [
      {
        component_path: "numeric.valuation",
        component_type: "valuation",
        issue_codes: ["numeric.valuation.unknown_calculation"],
      },
    ],
    snapshots: [
      {
        phase: "initial",
        method: "tool_call",
        reason_code: "semantic_validation",
        validation_issues: ["semantic.numeric.valuation.invalid"],
        schema_valid: false,
        candidate: { marker: "initial-value" },
        candidate_digest: "a".repeat(64),
      },
      {
        phase: "repair",
        method: "tool_call_recovered",
        reason_code: "semantic_validation",
        validation_issues: ["semantic.numeric.valuation.unknown_calculation"],
        schema_valid: true,
        candidate: {
          valuation_assessment: { method: "repair-value" },
        },
        candidate_digest: "b".repeat(64),
      },
    ],
  };
  degraded.result!.warnings = [
    {
      code: "decision.numeric_audit_incomplete",
      message: "Optional numeric conclusions were omitted.",
      evidence_ref: null,
      source: "committee.final.serialize.numeric",
    },
  ];
  vi.mocked(api.run).mockResolvedValue(degraded);

  render(
    <Router initialPath="/timelines/NVDA?node=run-1&view=decision">
      <ReaderFixture />
    </Router>,
  );

  expect(
    await screen.findByText(
      "Optional valuation and market-reference figures were omitted; the qualitative decision remains audited.",
    ),
  ).toBeVisible();
  expect(screen.getByText("Optional numeric conclusions were omitted.")).toBeVisible();
  expect(screen.queryByText("Decision-critical calculation audit")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "More" }));
  fireEvent.click(screen.getByRole("link", { name: "Run & diagnostics" }));
  await screen.findByRole("heading", { name: "Decision-critical calculation audit" });
  expect(screen.queryByText(/repair-value/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("Audit snapshots"));
  fireEvent.click(screen.getByRole("button", { name: "Raw record: Raw candidate" }));
  expect(await screen.findByText(/repair-value/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Initial candidate" }));
  expect(await screen.findByText(/initial-value/)).toBeVisible();

});

test("opens a locked Full clone template instead of rerunning immediately", async () => {
  render(
    <Router initialPath="/timelines/NVDA?node=run-1">
      <ReaderFixture />
      <LocationProbe />
    </Router>,
  );

  fireEvent.click(await screen.findByRole("button", { name: "More" }));
  fireEvent.click(
    await screen.findByRole("link", { name: "Reuse configuration for Full Research" }),
  );

  expect(screen.getByTestId("router-location")).toHaveTextContent(
    "/runs/new?intent=clone_full&from_run=run-1",
  );
  expect(api.action).not.toHaveBeenCalled();
});

test("dispatches Incremental research to its own summary and root-baseline update flow", async () => {
  const incremental = structuredClone(detail) as RunDetailType;
  incremental.run.research_kind = "incremental";
  incremental.run.full_baseline_run_id = "full-baseline";
  incremental.run.is_research_node = true;
  incremental.research_node = {
    id: "run-1",
    instrument: "NVDA",
    analysis_date: "2026-07-24",
    research_kind: "incremental",
    full_baseline_run_id: "full-baseline",
    research_schema_version: "1",
    information_cutoff_at: "2026-07-24T20:00:00Z",
    method_snapshot: { llm_provider: "openai", deep_model: "gpt-5.5" },
    decision: incremental.result!.decision,
    is_active: true,
    is_primary: true,
    is_cycle_head: true,
    cycle_warning: false,
    collection_summary: { domains: [] },
    research_availability: { domains: [] },
    information_advancement: {
      advanced: true,
      reasons: ["completed_stock_session"],
    },
    performance: {
      stock: {
        status: "calculated",
        calculation: performanceCalculation(0.12),
      },
      benchmarks: [
        {
          name: "S&P 500",
          component: {
            status: "calculated",
            calculation: performanceCalculation(0.04),
          },
          reported_difference: 0.08,
        },
        {
          name: "NASDAQ 100",
          component: {
            status: "calculated",
            calculation: performanceCalculation(0.06),
          },
          reported_difference: 0.06,
        },
      ],
    },
    reassessment: {
      entries: [
        {
          component_id: "thesis",
          disposition: "weakened",
          reason: "The new filing adds uncertainty.",
          evidence_refs: ["ev_baseline0001"],
        },
        {
          component_id: "risks.0",
          disposition: "reaffirmed",
          reason: "Demand risk remains.",
          evidence_refs: ["ev_0123456789ab"],
        },
      ],
    },
    decision_outcome: "updated",
    decision_outcome_reason: "The filing requires a new Decision thesis.",
    full_research_required_reasons: [
      { code: "scope_gap", message: "A complete refresh would resolve the scope gap." },
    ],
  } as never;
  incremental.incremental_context = {
    analysis_brief: {
      markdown: "# Key update\n\nThe filing changes the outlook.[^ev_baseline0001]",
      report_sections: [
        {
          id: "incremental.section_1",
          title: "Key update",
          anchor: "key-update",
          source_refs: ["ev_baseline0001"],
        },
      ],
      evidence_refs: ["ev_baseline0001"],
      warnings: [],
      prompt_version: "incremental-analysis-brief-v1",
      generation_method: "markdown_audited",
    },
    full_baseline: {
      run_id: "full-baseline",
      analysis_date: "2026-07-20",
      decision: detail.result!.decision!,
    },
  };
  const baselineEvidence = structuredClone(detail.result!.evidence!);
  baselineEvidence.digest = "baseline-digest";
  baselineEvidence.items[0].ref = "ev_baseline0001";
  vi.mocked(api.run).mockResolvedValue(incremental);
  vi.mocked(api.evidence).mockImplementation(async (runId) => {
    expect(runId).toBe("full-baseline");
    return baselineEvidence;
  });

  render(
    <Router initialPath="/timelines/NVDA?node=run-1">
      <ReaderFixture />
    </Router>,
  );

  expect(await screen.findByRole("heading", { name: "Analysis brief" })).toBeVisible();
  expect(screen.queryByRole("heading", { name: "Reassessment" })).not.toBeInTheDocument();
  expect(screen.queryByText("Complete judgment")).not.toBeInTheDocument();
  expect(screen.getByText("Overall assessment updated.")).toBeVisible();
  expect(screen.getByText("The filing requires a new Decision thesis.")).toBeVisible();
  expect(
    screen.getByText("This update recommends new full research."),
  ).toBeVisible();
  expect(screen.getByText("A complete refresh would resolve the scope gap.")).toBeVisible();
  expect(screen.getAllByText("Current instrument")[0]).toBeVisible();
  expect(screen.getAllByText("S&P 500")[0]).toBeVisible();
  expect(screen.getAllByText("NASDAQ 100")[0]).toBeVisible();
  expect(screen.getAllByText("Instrument minus benchmark")).toHaveLength(2);
  expect(screen.getByText("+8 pp")).toBeVisible();
  expect(screen.getByText("+6 pp")).toBeVisible();
  expect(screen.getAllByText("Jul 20, 2026 → Jul 24, 2026")).toHaveLength(3);
  expect(screen.getAllByText(/split-adjusted close/)).toHaveLength(3);
  expect(screen.getByRole("link", { name: "Analysis brief" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  expect(screen.queryByRole("link", { name: "Research reports" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Deliberation" })).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Analysis brief" })).toBeVisible();
  expect(screen.getByRole("link", { name: "Reassessment" })).toBeVisible();
  expect(screen.getByRole("link", { name: "Evidence updates" })).toBeVisible();
  expect(screen.queryByRole("link", { name: "Run progress" })).toBeNull();
  expect(
    within(screen.getByRole("navigation", { name: "Research run views" })).getAllByRole("link").map((link) => link.textContent),
  ).toEqual([
    "Analysis brief",
    "Reassessment",
    "Full assessment",
    "Evidence updates",
  ]);
  fireEvent.click(screen.getByRole("link", { name: "Full assessment" }));
  await waitFor(() =>
    expect(screen.getByRole("region", { name: "Full assessment" })).toHaveAttribute("id", "run-view-decision"),
  );
  expect(
    within(screen.getByRole("region", { name: "Full assessment" })).getByText("Overall assessment updated."),
  ).toBeVisible();
  expect(screen.queryByText("Current instrument")).not.toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Update this research" })).toHaveAttribute(
    "href",
    "/runs/new?intent=update&from_run=run-1&full_baseline_run_id=full-baseline",
  );

  fireEvent.click(screen.getByRole("link", { name: "Analysis brief" }));
  await waitFor(() => expect(api.evidence).toHaveBeenCalledTimes(1));
  await screen.findByRole("heading", { name: "Key update" });
  await waitFor(() => expect(screen.getByRole("heading", { name: "Key update" })).toBeVisible());
  expect(screen.getByRole("heading", { name: "Key update" }).closest("article")).toHaveTextContent(
    "The filing changes the outlook.",
  );

  fireEvent.click(screen.getByRole("link", { name: "Full assessment" }));
  expect(await screen.findByRole("heading", { name: "Executive summary" })).toBeVisible();
  expect(screen.getByText("Balanced research summary.")).toBeVisible();
  expect(screen.queryByRole("heading", { name: "Performance" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "More" }));
  fireEvent.click(screen.getByRole("link", { name: "Run & diagnostics" }));
  await screen.findByText("Decision-critical calculation audit");
  expect(screen.getByText("Observed market anchor")).toBeVisible();
  expect(screen.getByText("calc_market_reference")).not.toBeVisible();

  fireEvent.click(screen.getAllByRole("link", { name: "Read research" })[0]);
  fireEvent.click(await screen.findByRole("link", { name: "Reassessment" }));
  expect(await screen.findByText("The new filing adds uncertainty.")).toBeVisible();
  expect(screen.getByText("1 changed · 1 total")).toBeVisible();
  const reaffirmedSummary = screen.getByText("Show 1 reaffirmed item");
  expect(reaffirmedSummary).toBeInTheDocument();
  expect(reaffirmedSummary.closest("details")).not.toHaveAttribute("open");
  const reassessmentPanel = screen
    .getByRole("heading", { name: "Reassessment" })
    .closest("article");
  expect(within(reassessmentPanel!).queryByText("Audit details")).toBeNull();
  expect(within(reassessmentPanel!).queryByText("Technical mapping")).toBeNull();
});

test("keeps baseline Evidence out of Evidence updates and supports historical briefs", async () => {
  const incremental = structuredClone(detail) as RunDetailType;
  incremental.run.research_kind = "incremental";
  incremental.run.full_baseline_run_id = "full-baseline";
  incremental.research_node = {
    id: "run-1",
    instrument: "NVDA",
    analysis_date: "2026-07-24",
    research_kind: "incremental",
    full_baseline_run_id: "full-baseline",
    research_schema_version: "1",
    information_cutoff_at: "2026-07-24T20:00:00Z",
    method_snapshot: {},
    is_active: true,
    is_primary: true,
    is_cycle_head: true,
    is_baseline_compatible: true,
    cycle_id: "full-baseline",
    collection_summary: {
      version: "1",
      market: "united_states",
      domains: [
        {
          domain: "news",
          state: "partial",
          sources: [
            {
              source: "fixture.news",
              fallback: true,
              retrieved_at: "2026-07-24T19:00:00Z",
              diagnostic: { code: "coverage.partial" },
            },
          ],
          diagnostic: { code: "collection.partial" },
        },
      ],
    },
    research_availability: { version: "1", domains: [] },
    information_advancement: { advanced: false, reasons: [] },
    performance: null,
    reassessment: { entries: [] },
    full_research_required_reasons: [],
  } as never;
  incremental.incremental_context = {
    analysis_brief: null,
    full_baseline: {
      run_id: "full-baseline",
      analysis_date: "2026-07-20",
      decision: detail.result!.decision!,
    },
  };
  incremental.result!.evidence!.items[0].quality = "unavailable";
  vi.mocked(api.run).mockResolvedValue(incremental);

  render(
    <Router initialPath="/timelines/NVDA?node=run-1&view=evidence">
      <ReaderFixture />
    </Router>,
  );

  expect(await screen.findByRole("heading", { name: "Materials and limitations" })).toBeVisible();
  expect(api.evidence).not.toHaveBeenCalled();
  expect(screen.getByRole("heading", { name: "fixture · alternate-fixture" })).toBeVisible();
  expect(screen.queryByText("fixture.news")).toBeNull();
  expect(screen.getAllByText("Fallback").length).toBeGreaterThan(0);
  const collectionSummary = screen
    .getByRole("heading", { name: "Materials and limitations" })
    .closest("section");
  expect(within(collectionSummary!).queryByText("Collection diagnostics")).not.toBeInTheDocument();

  const evidenceCard = document.querySelector<HTMLElement>(".evidence-card");
  expect(evidenceCard).not.toBeNull();
  expect(within(evidenceCard!).getByText("Source")).toBeVisible();
  expect(within(evidenceCard!).getByText("Effective date")).toBeVisible();
  expect(within(evidenceCard!).getByText("Unavailable")).toBeVisible();
  expect(within(evidenceCard!).queryByText("Evidence metadata")).toBeNull();
  expect(within(evidenceCard!).queryByText("Canonical IDs and provenance")).toBeNull();
  fireEvent.click(
    within(evidenceCard!).getByRole("button", { name: "View source details" }),
  );
  expect(await screen.findByRole("dialog", { name: "Source details" })).toBeVisible();
  expect(within(screen.getByRole("dialog")).queryByText("Canonical IDs and provenance")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));

  fireEvent.click(screen.getByRole("link", { name: "Analysis brief" }));
  expect(
    await screen.findByText("This historical run did not record an analysis brief."),
  ).toBeVisible();
  await waitFor(() => expect(screen.getByText("The overall assessment outcome was not recorded.")).toBeVisible());
  fireEvent.click(screen.getByRole("link", { name: "Full assessment" }));
  await waitFor(() => expect(screen.getByText("The overall assessment outcome was not recorded.")).toBeVisible());
  await waitFor(() => expect(api.evidence).toHaveBeenCalledWith("full-baseline"));
});

test("explains when an Incremental Decision is inherited unchanged", async () => {
  vi.mocked(api.run).mockResolvedValue(
    incrementalDetailWithPerformanceReason("benchmark_unavailable"),
  );

  render(
    <Router initialPath="/timelines/NVDA?node=run-1">
      <ReaderFixture />
    </Router>,
  );

  expect(
    await screen.findByText("Overall assessment unchanged; baseline assessment retained."),
  ).toBeVisible();
  expect(screen.getByText("The baseline Decision remains valid as written.")).toBeVisible();
  fireEvent.click(screen.getByRole("link", { name: "Full assessment" }));
  expect(
    await screen.findByText("Overall assessment unchanged; baseline assessment retained."),
  ).toBeInTheDocument();
  await waitFor(() => expect(screen.getByText("The baseline Decision remains valid as written.")).toBeVisible());
});

test("shows when an earlier Incremental node keeps the Cycle Warning active", async () => {
  const incremental = incrementalDetailWithPerformanceReason("benchmark_unavailable");
  incremental.research_node!.cycle_warning = true;
  vi.mocked(api.run).mockResolvedValue(incremental);

  render(
    <Router initialPath="/timelines/NVDA?node=run-1">
      <ReaderFixture />
    </Router>,
  );

  expect(await screen.findByText("Full research recommended")).toBeVisible();
  expect(
    screen.getByText(
      "An earlier update in this cycle recommended new full research.",
    ),
  ).toBeVisible();
  expect(
    screen.queryByText("This update recommends new full research."),
  ).not.toBeInTheDocument();
});

test.each([
  ["en", "Performance reason is unavailable."],
  ["zh-CN", "表现原因不可用。"],
  ["ja", "パフォーマンス理由を利用できません。"],
])(
  "uses a localized fallback instead of an unknown performance reason in %s",
  async (language, fallback) => {
    const unknownReason = "A future backend performance reason.";
    await act(() => i18n.changeLanguage(language));
    vi.mocked(api.run).mockResolvedValue(
      incrementalDetailWithPerformanceReason(unknownReason),
    );

    render(
      <Router initialPath="/timelines/NVDA?node=run-1">
        <ReaderFixture />
      </Router>,
    );

    expect(await screen.findByText(fallback)).toBeVisible();
    expect(screen.queryByText(unknownReason)).not.toBeInTheDocument();
  },
);

test("loads only Run Detail initially and refreshes open deliberation artifacts on SSE", async () => {
  render(
    <Router initialPath="/timelines/NVDA?node=run-1">
      <ReaderFixture />
    </Router>,
  );

  expect(await screen.findByRole("link", { name: "Overview" })).toBeVisible();
  expect(api.capabilities).not.toHaveBeenCalled();
  expect(api.artifacts).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("link", { name: "Deliberation" }));
  await waitFor(() => expect(api.artifacts).toHaveBeenCalledTimes(1));

  act(() =>
    FakeEventSource.instance.emit("artifact.created", {
      run_id: "run-1",
      sequence: 8,
      attempt: 1,
      event_type: "artifact.created",
      node: "judge.research",
      payload: { artifact_id: "artifact-new" },
      created_at: "2026-07-24T00:01:10Z",
    } as RunEvent),
  );

  await waitFor(() => expect(api.artifacts).toHaveBeenCalledTimes(2));
});

test("shows run metrics only in the Diagnostics view", async () => {
  render(
    <Router initialPath="/timelines/NVDA?node=run-1">
      <ReaderFixture />
    </Router>,
  );

  await screen.findByRole("link", { name: "Overview" });
  expect(screen.queryByText("Attempt metrics")).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("button", { name: "More" }));
  fireEvent.click(screen.getByRole("link", { name: "Run & diagnostics" }));
  await screen.findByRole("heading", { name: "Run metrics and diagnostics" });
  expect(screen.getByText("12.4s", { selector: ".metrics-strip strong" })).toBeVisible();
  expect(screen.getByRole("heading", { name: "Attempt metrics" })).toBeVisible();
});

test("shows report limitations without technical audit controls", async () => {
  render(<Router initialPath="/timelines/NVDA?node=run-1&view=reports&report=market"><ReaderFixture /></Router>);
  await screen.findByRole("heading", { name: "Market report" });
  expect(screen.getByText("Historical source was partial.")).toBeVisible();
  expect(screen.queryByText("Audit details")).not.toBeInTheDocument();
});

test("labels runs that have no recorded artifacts", async () => {
  vi.mocked(api.artifacts).mockResolvedValue([]);
  render(
    <Router initialPath="/timelines/NVDA?node=run-1">
      <ReaderFixture />
    </Router>,
  );

  expect(await screen.findByRole("link", { name: "Overview" })).toBeVisible();
  fireEvent.click(screen.getByRole("link", { name: "Deliberation" }));

  expect(
    await screen.findByText(
      "No typed research artifacts were recorded for this run.",
    ),
  ).toBeVisible();
});

test("groups metrics by role and expands phase observations", async () => {
  const detailWithLargeRoleTotals = structuredClone(detail);
  const roleNodes = detailWithLargeRoleTotals.run.metrics!.node_metrics!;
  roleNodes["committee.final.reason"].input_tokens = 13_003;
  roleNodes["committee.final.reason"].output_tokens = 5_000;
  roleNodes["committee.final.serialize"].input_tokens = 14_303;
  roleNodes["committee.final.serialize"].output_tokens = 7_345;
  vi.mocked(api.run).mockResolvedValue(detailWithLargeRoleTotals);

  render(
    <Router initialPath="/timelines/NVDA?node=run-1">
      <ReaderFixture />
    </Router>,
  );

  expect(await screen.findByRole("link", { name: "Overview" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "More" }));
  fireEvent.click(screen.getByRole("link", { name: "Run & diagnostics" }));
  await screen.findByRole("heading", { name: "Run metrics and diagnostics" });
  const roleMetricsTitle = screen.getByText("Metrics by role");
  const roleMetrics = roleMetricsTitle.closest("section");
  expect(roleMetricsTitle).toBeVisible();
  const coverageMetric = screen.getByText("Token detail coverage", {
    selector: ".metrics-strip span",
  }).parentElement;
  expect(coverageMetric).toHaveTextContent("1/4");
  expect(coverageMetric).toHaveAttribute(
    "title",
    expect.stringContaining("cache or reasoning-token details"),
  );
  const summary = within(roleMetrics!).getByText("Final committee");
  const details = summary.closest("details");
  const roleSummary = summary.closest("summary");
  expect(details).not.toHaveAttribute("open");
  expect(roleSummary).toHaveTextContent("2 LLM calls");
  expect(roleSummary).toHaveTextContent("27,306 input tokens");
  expect(roleSummary).toHaveTextContent("12,345 output tokens");
  expect(roleSummary).not.toHaveTextContent("Reasoning");
  expect(roleSummary).not.toHaveTextContent("39,651 tokens");
  expect(summary.closest("strong")).toBeNull();
  expect(
    roleSummary!.querySelector(".metric-disclosure-arrow"),
  ).toHaveTextContent("›");

  fireEvent.click(summary);
  expect(details).toHaveAttribute("open");
  act(() => {
    FakeEventSource.instance.emit("phase.started", {
      run_id: "run-1",
      sequence: 10,
      attempt: 1,
      event_type: "phase.started",
      node: "committee.final.reason",
      payload: {},
      created_at: "2026-07-24T00:00:10Z",
    });
    FakeEventSource.instance.emit("phase.started", {
      run_id: "run-1",
      sequence: 20,
      attempt: 1,
      event_type: "phase.started",
      node: "analyst.market.collect",
      payload: {},
      created_at: "2026-07-24T00:00:20Z",
    });
  });
  const committee = within(details!)
    .getByText("committee.final.reason")
    .closest("tr");
  const analystDetails = within(roleMetrics!)
    .getByText("Market")
    .closest("details");
  fireEvent.click(within(analystDetails!).getByText("Market"));
  const analyst = within(analystDetails!)
    .getByText("analyst.market.collect")
    .closest("tr");
  expect(committee).toHaveTextContent("13,003");
  expect(committee).toHaveTextContent("5,000");
  expect(within(committee!).getAllByRole("cell")[13]).toHaveTextContent("Not recorded");
  expect(committee).toHaveTextContent("2.5s");
  expect(analyst).toHaveTextContent("300");
  expect(analyst).toHaveTextContent("100");
  expect(analyst).toHaveTextContent("1/1");
  expect(analyst).toHaveTextContent("Not recorded");
  expect(details).toHaveTextContent("Schema serialization");
  expect(details).toHaveTextContent("Not recorded");
  expect(
    details!.compareDocumentPosition(analystDetails!) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).not.toBe(0);

  act(() => {
    FakeEventSource.instance.emit("node.context_prepared", {
      run_id: "run-1",
      sequence: 30,
      attempt: 1,
      event_type: "node.context_prepared",
      node: "committee.final.context",
      payload: {
        inline_characters: 12345,
        reference_count: 7,
        table_summary_count: 2,
        catalog_items: 43,
      },
      created_at: "2026-07-24T00:00:30Z",
    });
    FakeEventSource.instance.emit("node.output_recovered", {
      run_id: "run-1",
      sequence: 31,
      attempt: 1,
      event_type: "node.output_recovered",
      node: "committee.final.reason",
      payload: { method: "json_mode_recovered" },
      created_at: "2026-07-24T00:00:31Z",
    });
  });
  expect(committee).toHaveTextContent("Recovered");
  const contextSummary = screen.getByText("Prepared contexts", {
    exact: false,
  });
  fireEvent.click(contextSummary);
  const contextDetails = contextSummary.closest("details");
  const contextHeader = contextSummary.closest("summary");
  expect(contextHeader).toHaveTextContent(
    "Deterministic role contexts in timeline order; preparing them does not call a model.",
  );
  expect(
    contextDetails!.querySelector(".metrics-observation-note"),
  ).toBeNull();
  expect(contextDetails).toHaveTextContent("committee.final.context");
  expect(contextDetails).toHaveTextContent("12,345");
  expect(contextDetails).toHaveTextContent("43");

  const attemptSummary = screen.getByRole("heading", { name: "Attempt metrics" });
  const attemptDetails = attemptSummary.closest("section");
  expect(attemptSummary).toBeVisible();
  const attemptRow = within(attemptDetails!).getByText(
    "Succeeded",
  ).closest("tr");
  expect(attemptRow).toHaveTextContent("Succeeded");
  expect(attemptRow).toHaveTextContent("1,200");
  expect(attemptRow).toHaveTextContent("12.4s");
});

test("keeps report footnote navigation in an in-page source drawer", async () => {
  const initialPath = "/timelines/NVDA?node=run-1&view=reports&report=news";
  const view = render(
    <Router initialPath={initialPath}>
      <ReaderFixture />
      <LocationProbe />
    </Router>,
  );

  await waitFor(() => expect(screen.getByRole("heading", { name: "News report" })).toBeVisible());
  fireEvent.click(
    screen.getAllByRole("button", {
      name: "Open evidence ev_0123456789ab",
    })[0],
  );
  expect(screen.getByRole("dialog", { name: "Source details" })).toBeVisible();
  expect(screen.queryByText("Canonical IDs and provenance")).not.toBeInTheDocument();
  expect(screen.getByTestId("router-location")).toHaveTextContent(
    "/timelines/NVDA?node=run-1&view=reports&report=news",
  );
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  await waitFor(() => expect(screen.getByRole("heading", { name: "News report" })).toBeVisible());
  fireEvent.click(
    screen.getAllByRole("button", {
      name: "Open evidence ev_0123456789ab",
    })[0],
  );
  fireEvent.click(screen.getByRole("button", { name: "Close" }));

  const restoredPath =
    screen.getByTestId("router-location").textContent ?? initialPath;
  view.unmount();
  render(
    <Router initialPath={restoredPath}>
      <ReaderFixture />
    </Router>,
  );

  await waitFor(() => expect(screen.getByRole("heading", { name: "News report" })).toBeVisible());
});

test("localizes canonical report labels for zh-CN", async () => {
  await act(() => i18n.changeLanguage("zh-CN"));
  render(
    <Router initialPath="/timelines/NVDA?node=run-1&view=reports">
      <ReaderFixture />
    </Router>,
  );

  await waitFor(() => {
    expect(
      screen.getByRole("heading", { name: "Fundamentals report" }),
    ).toBeVisible();
  });
  const labels = ["基本面", "市场", "新闻", "舆情"].map((name) =>
    screen.getByRole("link", { name }),
  );
  labels.slice(0, -1).forEach((label, index) => {
    expect(
      label.compareDocumentPosition(labels[index + 1]) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).not.toBe(0);
  });
});

test("returns from the evidence tab to the report the reader was viewing", async () => {
  render(<Router initialPath="/timelines/NVDA?node=run-1&view=reports&report=market"><ReaderFixture /><LocationProbe /></Router>);
  await screen.findByRole("heading", { name: "Market report" });
  fireEvent.click(screen.getByRole("link", { name: "Evidence" }));
  fireEvent.click(await screen.findByRole("button", { name: /Return to reports/ }));
  expect(screen.getByTestId("router-location")).toHaveTextContent("view=reports&report=market");
});

test("filters evidence locally by text and source without changing research", async () => {
  render(<Router initialPath="/timelines/NVDA?node=run-1&view=evidence"><ReaderFixture /></Router>);
  const search = await screen.findByRole("searchbox", { name: "Search evidence" });
  expect(document.querySelectorAll(".evidence-card").length).toBeGreaterThan(0);
  fireEvent.change(search, { target: { value: "no matching evidence body" } });
  expect(document.querySelectorAll(".evidence-card")).toHaveLength(0);
  expect(screen.getByText("No evidence matches these filters.")).toBeVisible();
  fireEvent.change(search, { target: { value: "" } });
  expect(document.querySelectorAll(".evidence-card").length).toBeGreaterThan(0);
  expect(screen.getByRole("combobox", { name: "Source filter" })).toBeVisible();
});

vi.mock("../../shared/api/client", () => ({
  api: {
    run: vi.fn(),
    analysisCutoffContext: vi.fn(),
    evidence: vi.fn(),
    artifacts: vi.fn(),
    action: vi.fn(),
    capabilities: vi.fn(),
    restoreRuns: vi.fn(),
  },
}));

