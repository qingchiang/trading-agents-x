import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import {
  api,
  type Capabilities,
  type ResearchArtifact,
  type ResearchNodeView,
  type RunDetail as RunDetailType,
  type RunEvent,
} from "../api/client";
import i18n from "../i18n";
import { Router, useLocation } from "../router";
import RunDetail from "./RunDetail";

vi.mock("../api/client", () => ({
  api: {
    run: vi.fn(),
    evidence: vi.fn(),
    artifacts: vi.fn(),
    action: vi.fn(),
    capabilities: vi.fn(),
    restoreRuns: vi.fn(),
  },
}));

class FakeEventSource {
  static instance: FakeEventSource;
  static instances: FakeEventSource[] = [];
  listeners = new Map<string, EventListener>();
  onerror: ((event: Event) => void) | null = null;
  closed = false;
  url: string;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instance = this;
    FakeEventSource.instances.push(this);
  }

  addEventListener(name: string, listener: EventListener) {
    this.listeners.set(name, listener);
  }

  close() {
    this.closed = true;
  }

  emit(name: string, event: RunEvent) {
    this.listeners.get(name)?.(
      new MessageEvent(name, { data: JSON.stringify(event) }),
    );
  }
}

function analystReport(
  analyst: "fundamentals" | "market" | "news" | "social",
  title: string,
  warnings: unknown[] = [],
) {
  return {
    analyst,
    markdown: `# ${title} report\n\nEvidence-grounded narrative.[^ev_0123456789ab]`,
    report_sections: [
      {
        id: "overview",
        title: `${title} report`,
        anchor: "overview",
        source_refs: ["ev_0123456789ab"],
      },
    ],
    confidence: 0.7,
    key_claims: [
      {
        id: `${analyst}.claim_1`,
        section_id: "overview",
        kind: "inference",
        importance: "primary",
        statement: "Evidence is mixed.",
        implication: "The conclusion should preserve uncertainty.",
        confidence: 0.7,
        evidence_refs: ["ev_0123456789ab"],
      },
    ],
    source_refs: ["ev_0123456789ab"],
    audit_status: "complete",
    warnings,
  };
}

function LocationProbe() {
  const location = useLocation();
  return (
    <output data-testid="router-location">
      {location.pathname}
      {location.search}
      {location.hash}
    </output>
  );
}

const runMetrics = {
  llm_calls: 4,
  tool_calls: 3,
  input_tokens: 1200,
  output_tokens: 400,
  cache_hit_input_tokens: 300,
  cache_miss_input_tokens: 900,
  reasoning_output_tokens: 150,
  detailed_usage_calls: 1,
  wall_time_seconds: 12.4,
  node_metrics: {
    "analyst.market.collect": {
      llm_calls: 1,
      tool_calls: 3,
      input_tokens: 300,
      output_tokens: 100,
      cache_hit_input_tokens: 100,
      cache_miss_input_tokens: 200,
      reasoning_output_tokens: 40,
      detailed_usage_calls: 1,
      wall_time_seconds: 2.1,
    },
    "analyst.market.report": {
      llm_calls: 1,
      tool_calls: 0,
      input_tokens: 500,
      output_tokens: 120,
      cache_hit_input_tokens: 200,
      cache_miss_input_tokens: 300,
      reasoning_output_tokens: 60,
      detailed_usage_calls: 0,
      wall_time_seconds: 2.9,
    },
    "committee.final.reason": {
      llm_calls: 1,
      tool_calls: 0,
      input_tokens: 200,
      output_tokens: 100,
      wall_time_seconds: 2.5,
    },
    "committee.final.serialize": {
      llm_calls: 1,
      tool_calls: 0,
      input_tokens: 200,
      output_tokens: 180,
      wall_time_seconds: 2,
    },
  },
};

const detail = {
  run: {
    id: "run-1",
    source_run_id: null,
    instrument_name: "NVIDIA Corporation",
    trashed_at: null,
    status: "succeeded",
    request: {
      ticker: "NVDA",
      analysis_date: "2026-07-24",
      asset_type: "stock",
      profile: "standard",
      analysts: ["market"],
      llm_provider: "openai",
      quick_model: "gpt-5.4-mini",
      deep_model: "gpt-5.5",
      quick_reasoning_effort: "provider_default",
      deep_reasoning_effort: "provider_default",
      output_language: "ja",
    },
    config_snapshot: {},
    attempt: 1,
    cancel_requested: false,
    metrics: runMetrics,
    created_at: "2026-07-24T00:00:00Z",
    updated_at: "2026-07-24T00:01:00Z",
  },
  result: {
    run_id: "run-1",
    status: "succeeded",
    instrument: "NVDA",
    reports: {
      social: analystReport("social", "Social"),
      news: analystReport("news", "News"),
      market: {
        ...analystReport("market", "Market", [
          {
            code: "evidence.degraded",
            message: "Historical source was partial.",
            evidence_ref: "ev_0123456789ab",
            source: "fixture",
          },
        ]),
        source_refs: [
          "ev_0123456789ab",
          "ev_fedcba987654",
        ],
        markdown:
          "# Market report\n\nCompare [^ev_0123456789ab] with [^ev_fedcba987654].",
        report_sections: [
          {
            id: "overview",
            title: "Market report",
            anchor: "overview",
            source_refs: [
              "ev_0123456789ab",
              "ev_fedcba987654",
            ],
          },
        ],
      },
      fundamentals: analystReport("fundamentals", "Fundamentals"),
    },
    decision: {
      rating: "Hold",
      confidence: "medium",
      executive_summary: "Balanced research summary.",
      thesis: "Evidence is balanced.",
      evidence_refs: ["ev_0123456789ab"],
      catalysts: [],
      risks: ["Demand slows"],
      invalidation_conditions: ["New filing changes the thesis"],
      unresolved_questions: [],
      time_horizon: "6-12 months",
      scenarios: (["base", "bull", "bear"] as const).map((kind) => ({
        kind,
        core_assumptions: ["Current evidence remains representative."],
        outcome: `${kind} outcome.`,
        evidence_refs: ["ev_0123456789ab"],
        reference_ranges:
          kind === "base"
            ? [
                {
                  category: "technical" as const,
                  label: "Technical support",
                  low: {
                    value: 90,
                    basis: "interpreted" as const,
                    evidence_refs: ["ev_0123456789ab"],
                    date_evidence_refs: ["ev_0123456789ab"],
                    as_of_date: "2026-07-24",
                  },
                  high: {
                    value: 105,
                    basis: "interpreted" as const,
                    evidence_refs: ["ev_0123456789ab"],
                    date_evidence_refs: ["ev_0123456789ab"],
                    as_of_date: "2026-07-24",
                  },
                  unit: "USD",
                  interpretation: "Technical range from the sealed evidence.",
                  limitations: ["Not a valuation."],
                },
                {
                  category: "analyst_consensus" as const,
                  label: "Analyst target range",
                  low: {
                    value: 95,
                    basis: "interpreted" as const,
                    evidence_refs: ["ev_0123456789ab"],
                    date_evidence_refs: ["ev_0123456789ab"],
                    as_of_date: "2026-07-24",
                  },
                  high: {
                    value: 125,
                    basis: "interpreted" as const,
                    evidence_refs: ["ev_0123456789ab"],
                    date_evidence_refs: ["ev_0123456789ab"],
                    as_of_date: "2026-07-24",
                  },
                  unit: "USD",
                  interpretation: "Consensus range from the sealed evidence.",
                  limitations: ["Coverage may change."],
                },
              ]
            : [],
      })),
      market_reference_levels: [
        {
          label: "Recent close",
          value: 4199.4116,
          unit: "JPY",
          as_of_date: "2026-07-24",
          interpretation: "Observed reference, not an execution instruction.",
          evidence_refs: ["ev_0123456789ab"],
          date_evidence_refs: ["ev_0123456789ab"],
          basis: "observed",
          calculation_ids: [],
          temporal_basis: "live_snapshot",
        },
      ],
      calculation_records: [
        {
          id: "calc_market_reference",
          formula: "close",
          inputs: { close: 100 },
          input_evidence_refs: ["ev_0123456789ab"],
          result: 100,
          unit: "USD",
          as_of_date: "2026-07-24",
          limitations: ["One point-in-time market observation."],
          decision_uses: [
            {
              component_path: "thesis",
              label: "Observed market anchor",
            },
          ],
        },
      ],
    },
    evidence: {
      version: "8",
      instrument: "NVDA",
      analysis_date: "2026-07-24",
      sealed_at: "2026-07-24T00:00:30Z",
      digest: "fixture-digest",
      items: [
        {
          ref: "ev_0123456789ab",
          source: "fixture",
          evidence_type: "Price snapshot",
          requested_date: "2026-07-24",
          effective_date: "2026-07-24",
          available_at: "2026-07-24T00:00:00Z",
          content: "**Close:** 100 USD",
          value: 100,
          unit: "USD",
          quality: "high",
          fallback: false,
          provenance: { vendor: "fixture-feed" },
        },
        {
          ref: "ev_fedcba987654",
          source: "alternate-fixture",
          evidence_type: "Composite snapshot",
          requested_date: "2026-07-24",
          effective_date: "2026-07-24",
          available_at: "2026-07-24T00:00:01Z",
          content: "**Close:** 100 USD",
          value: 100,
          unit: "USD",
          quality: "low",
          fallback: true,
          provenance: { vendor: "alternate-feed" },
        },
      ],
    },
    metrics: runMetrics,
    recoveries: [
      {
        attempt: 1,
        node: "debate.agenda.serialize",
        initial_reason_code: "non_json_response",
        recovery_method: "tool_call_recovered",
        validation_issue_codes: [],
        retry_count: 1,
        recovered_at: "2026-07-24T00:00:45Z",
      },
    ],
    warnings: [
      {
        code: "run.fixture_warning",
        message: "One run-level fixture warning.",
        evidence_ref: null,
        source: "committee.final",
      },
    ],
  },
  attempts: [
    {
      attempt: 1,
      status: "succeeded",
      resume_count: 1,
      metrics: runMetrics,
      started_at: "2026-07-24T00:00:01Z",
      finished_at: "2026-07-24T00:01:00Z",
      error_code: null,
    },
  ],
  evidence_status: {
    status: "sealed",
    digest: "fixture-digest",
    item_count: 2,
    table_count: 0,
    sealed_attempt: 1,
    sealed_at: "2026-07-24T00:00:30Z",
  },
} as unknown as RunDetailType;

const artifacts = [
  {
    id: "artifact-bull",
    run_id: "run-1",
    attempt: 1,
    stage: "case",
    role: "bull",
    round: 0,
    schema_version: "1",
    generation_method: "tool_call",
    generation_observations: [
      {
        node: "committee.final.serialize",
        task_kind: "schema_serialization",
        client_role: "deep_serializer",
        generation_method: "tool_call",
      },
    ],
    created_at: "2026-07-24T00:00:40Z",
    content: {
      role: "bull",
      markdown: "**Demand** remains constructive.[^ev_0123456789ab]",
    },
  },
  {
    id: "artifact-judge",
    run_id: "run-1",
    attempt: 1,
    stage: "judge",
    role: "research_judge",
    round: 0,
    schema_version: "1",
    generation_method: "tool_call",
    created_at: "2026-07-24T00:00:50Z",
    content: {
      preliminary_rating: "Hold",
      confidence: 0.62,
      markdown: "The judge draft remains balanced.",
      issue_dispositions: [
        {
          issue_id: "debate.issue_1",
          status: "unresolved",
        },
      ],
    },
  },
] as unknown as ResearchArtifact[];

beforeEach(async () => {
  vi.resetAllMocks();
  FakeEventSource.instances = [];
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
  });
  localStorage.removeItem("tradingagents-timeline-order");
  localStorage.removeItem("tradingagents-audit-details-open");
  await i18n.changeLanguage("en");
  vi.mocked(api.run).mockResolvedValue(detail);
  vi.mocked(api.evidence).mockResolvedValue(detail.result!.evidence!);
  vi.mocked(api.artifacts).mockResolvedValue(artifacts);
  vi.mocked(api.capabilities).mockResolvedValue({
    defaults: { trash_retention_days: 30 },
  } as Capabilities);
  vi.stubGlobal("EventSource", FakeEventSource);
});

test("restores deliberation and resolves evidence references across run views", async () => {
  render(
    <Router initialPath="/runs/run-1">
      <RunDetail />
    </Router>,
  );

  expect(await screen.findByRole("heading", { name: "NVIDIA Corporation" })).toBeVisible();
  await waitFor(() => expect(FakeEventSource.instance).toBeDefined());
  expect(
    FakeEventSource.instance.listeners.has(
      "decision.numeric_display_scale_normalized",
    ),
  ).toBe(true);
  expect(screen.getByText("Run warnings")).toBeVisible();
  expect(
    screen.getByText("One run-level fixture warning."),
  ).not.toBeVisible();
  expect(screen.getByText("Structured recoveries")).toBeVisible();
  expect(screen.getByText("debate.agenda.serialize")).not.toBeVisible();
  fireEvent.click(screen.getByText("Structured recoveries"));
  expect(screen.getByText("debate.agenda.serialize")).toBeVisible();
  expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.queryByText("Run metrics and diagnostics")).toBeNull();
  fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
  fireEvent.click(await screen.findByText("Run metrics and diagnostics"));
  expect(screen.getAllByText("1,200")[0]).toBeVisible();
  expect(screen.getByText("1/4")).toBeVisible();
  expect(
    screen.getByRole("link", { name: "Export research package" }),
  ).toHaveAttribute(
    "href",
    "/api/v1/runs/run-1/export?format=package",
  );

  fireEvent.click(screen.getByRole("tab", { name: "Deliberation" }));
  expect(await screen.findByText("Demand")).toBeVisible();
  expect(screen.getByText("research judge")).toBeVisible();

  fireEvent.click(
    screen.getAllByRole("button", {
      name: "Open evidence ev_0123456789ab",
    })[0],
  );
  expect(
    await screen.findByRole("dialog", { name: "Source details" }),
  ).toBeVisible();
  expect(
    screen.getByRole("tab", { name: "Deliberation" }),
  ).toHaveAttribute("aria-selected", "true");
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  fireEvent.click(screen.getByRole("tab", { name: "Evidence" }));
  expect(
    await screen.findByRole("heading", {
      name: "Price snapshot · Composite snapshot",
    }),
  ).toBeVisible();
  expect(screen.getByText("E01")).toHaveAttribute(
    "title",
    "ev_0123456789ab\nev_fedcba987654",
  );
  expect(
    screen.getByText("1 unique bodies · 2 audit records"),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "View source details" }));
  fireEvent.click(screen.getByText("Canonical IDs and provenance"));
  expect(screen.getByText("fixture-feed", { exact: false })).toBeVisible();
  expect(screen.getByText("ev_fedcba987654")).toBeVisible();
  fireEvent.click(
    screen.getByRole("button", {
      name: "Copy evidence ID ev_fedcba987654",
    }),
  );
  expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
    "ev_fedcba987654",
  );
  fireEvent.click(screen.getByRole("button", { name: "Close" }));

  fireEvent.click(screen.getByRole("tab", { name: "Reports" }));
  const fundamentalsTab = await screen.findByRole("button", {
    name: "Fundamentals",
  });
  const marketTab = screen.getByRole("button", { name: "Market" });
  const newsTab = screen.getByRole("button", { name: "News" });
  const socialTab = screen.getByRole("button", { name: "Sentiment" });
  expect(
    fundamentalsTab.compareDocumentPosition(marketTab) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).not.toBe(0);
  expect(
    marketTab.compareDocumentPosition(newsTab) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).not.toBe(0);
  expect(
    newsTab.compareDocumentPosition(socialTab) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).not.toBe(0);
  await waitFor(() => {
    expect(
      screen.getByRole("heading", { name: "Fundamentals report" }),
    ).toBeVisible();
  });
  const reportScroller = document.querySelector<HTMLElement>(".analyst-report");
  expect(reportScroller).not.toBeNull();
  if (!reportScroller) throw new Error("report scroller not rendered");
  reportScroller.scrollTop = 137;
  fireEvent.scroll(reportScroller);
  expect(
    sessionStorage.getItem("tradingagents-report-scroll:run-1:fundamentals"),
  ).toBe("137");
  fireEvent.click(marketTab);
  expect(reportScroller.scrollTop).toBe(0);
  reportScroller.scrollTop = 42;
  fireEvent.scroll(reportScroller);
  fireEvent.click(fundamentalsTab);
  expect(reportScroller.scrollTop).toBe(137);
  fireEvent.click(marketTab);
  expect(reportScroller.scrollTop).toBe(42);
  expect(
    screen.queryByRole("heading", { name: "Data Provenance" }),
  ).not.toBeInTheDocument();
  expect(screen.getByText("Audit details")).toBeVisible();
  const reportAuditSummary = screen.getByText("Audit details").closest("summary");
  expect(reportAuditSummary?.querySelector(".details-chevron")).not.toBeNull();
  expect(
    await screen.findByText("Historical source was partial."),
  ).not.toBeVisible();
  fireEvent.click(screen.getByText("Audit details"));
  expect(screen.getByText("Historical source was partial.")).toBeVisible();
  const inlineRefs = screen.getAllByRole("button", {
    name: /Open evidence ev_/,
  });
  expect(inlineRefs.some((marker) => marker.textContent === "[E01]")).toBe(true);
  expect(
    screen.queryByText("ev_0123456789ab", { exact: true }),
  ).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("tab", { name: "Overview" }));
  expect(await screen.findByText("Evidence is balanced.")).toBeVisible();
  expect(screen.getByText("Technical support")).toBeVisible();
  expect(screen.getByText("Analyst target range")).toBeVisible();
  expect(screen.getByText("Analyst consensus")).toBeVisible();
  const auditSummaries = screen.getAllByText(
    "Decision-critical calculation audit",
  );
  expect(auditSummaries).toHaveLength(1);
  expect(
    auditSummaries[0].closest("summary")?.querySelector(".details-chevron"),
  ).not.toBeNull();
  fireEvent.click(auditSummaries[0]);
  const marketCalculation = screen.getByText("Observed market anchor").closest("article");
  expect(within(marketCalculation!).getByText("Thesis", { exact: true })).toBeVisible();
  expect(within(marketCalculation!).getByText("calc_market_reference")).not.toBeVisible();
  fireEvent.click(within(marketCalculation!).getByText("Formula and Evidence"));
  expect(within(marketCalculation!).getByText("calc_market_reference")).toBeVisible();
  const referenceTable = screen.getByRole("table", {
    name: "Market reference levels",
  });
  expect(within(referenceTable).getByText("4,199.41 JPY")).toBeVisible();
  expect(referenceTable).toHaveTextContent("Reference");
  expect(referenceTable).toHaveTextContent("Value");
  expect(referenceTable).toHaveTextContent("As-of date");
  expect(referenceTable).toHaveTextContent("Basis");
  expect(referenceTable).toHaveTextContent("Observed");
  expect(referenceTable).toHaveTextContent("Live snapshot");

  const artifactEvent = {
    run_id: "run-1",
    sequence: 6,
    attempt: 1,
    event_type: "artifact.created",
    node: "judge.research",
    payload: {
      artifact_id: "artifact-judge",
      stage: "judge",
      role: "research_judge",
    },
    created_at: "2026-07-24T00:00:50Z",
  } as RunEvent;
  act(() => FakeEventSource.instance.emit("artifact.created", artifactEvent));
  expect(api.run).toHaveBeenCalledTimes(1);
  expect(api.artifacts).toHaveBeenCalledTimes(1);

  const event = {
    run_id: "run-1",
    sequence: 7,
    attempt: 1,
    event_type: "run.succeeded",
    node: null,
    payload: {},
    created_at: "2026-07-24T00:01:00Z",
  } as RunEvent;
  act(() => FakeEventSource.instance.emit("run.succeeded", event));

  fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
  expect((await screen.findAllByText("Commit complete"))[0]).toBeVisible();
  expect(screen.getByText("judge.research")).toBeVisible();
  expect(screen.getByText("run.lifecycle")).toBeVisible();
  expect(screen.getByRole("button", { name: "Earliest first" })).toBeVisible();
  expect(FakeEventSource.instance.closed).toBe(true);
  await vi.waitFor(() => expect(api.run).toHaveBeenCalledTimes(2));
  expect(api.artifacts).toHaveBeenCalledTimes(1);
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
    <Router initialPath="/runs/run-1?view=decision">
      <RunDetail />
    </Router>,
  );

  expect(
    await screen.findByText(
      "Optional valuation and market-reference figures were omitted; the qualitative decision remains audited.",
    ),
  ).toBeVisible();
  expect(screen.getByText("Optional numeric conclusions were omitted.")).not.toBeVisible();

  const appendixSummary = screen.getByText(
    "Decision-critical calculation audit",
  );
  const appendix = appendixSummary.closest("details");
  expect(appendix).not.toHaveAttribute("open");
  fireEvent.click(appendixSummary);
  expect(screen.getByText(/repair-value/)).not.toBeVisible();
  fireEvent.click(screen.getByText("Raw candidate"));
  expect(screen.getByText("Valuation assessment")).toBeVisible();
  expect(screen.getByText(/repair-value/)).toBeVisible();
  expect(screen.queryByText(/initial-value/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "Initial candidate" }));
  expect(screen.getByText(/initial-value/)).toBeVisible();

  fireEvent.click(screen.getByRole("button", { name: "Review warnings" }));

  expect(screen.getByText("Optional numeric conclusions were omitted.")).toBeVisible();
});

test("shows decision audit gaps without an empty candidate viewer", async () => {
  const degraded = structuredClone(detail);
  degraded.result!.decision!.numeric_audit_status = "partial";
  degraded.result!.numeric_audit = {
    status: "partial",
    omitted_components: [
      {
        component_path: "thesis",
        component_type: "decision_claim",
        reference_label: "Forward PE",
        issue_codes: [
          "numeric.requirement_candidate.0.inputs.list_type",
          "numeric.requirement_candidate.0.limitations.missing",
        ],
      },
    ],
    snapshots: [],
  };
  vi.mocked(api.run).mockResolvedValue(degraded);

  render(
    <Router initialPath="/runs/run-1?view=decision">
      <RunDetail />
    </Router>,
  );

  const summary = await screen.findByText(
    "Decision-critical calculation audit",
  );
  fireEvent.click(summary);
  expect(screen.getByText("Decision claim · Forward PE")).toBeVisible();
  expect(
    screen.getByText("numeric.requirement_candidate.0.inputs.list_type"),
  ).toBeVisible();
  expect(
    screen.getByText("numeric.requirement_candidate.0.limitations.missing"),
  ).toBeVisible();
  expect(
    screen.getByText(
      "These decision-critical derived values were not fully verified. The qualitative decision is retained, but the listed values are excluded from canonical calculations.",
    ),
  ).toBeVisible();
  expect(
    screen.queryByText("The provider output could not be parsed as a JSON object."),
  ).not.toBeInTheDocument();
});

test("shows requirement comparisons separately from candidate drafts", async () => {
  const compared = structuredClone(detail);
  compared.result!.decision!.numeric_audit_status = "partial";
  compared.result!.numeric_audit = {
    status: "partial",
    requirement_checks: [
      {
        requirement_id: "req_forward_pe",
        calculation_id: "calc_forward_pe",
        component_path: "thesis",
        label: "Forward PE",
        stated_value: 45.8,
        fraction_digits: 1,
        unit: "x",
        formula: "price / eps",
        inputs: { price: 3834.343755, eps: 1 },
        input_evidence_refs: ["ev_0123456789ab"],
        canonical_result: 3834.343755,
        rounded_stated_value: 45.8,
        rounded_canonical_result: 3834.3,
        calculation_status: "verified",
        display_status: "mismatched",
        issue_codes: ["numeric.requirement.req_forward_pe.result_mismatch"],
      },
    ],
    omitted_components: [],
    snapshots: [],
  };
  vi.mocked(api.run).mockResolvedValue(compared);

  render(
    <Router initialPath="/runs/run-1?view=decision">
      <RunDetail />
    </Router>,
  );

  const summary = await screen.findByText("Decision-critical calculation audit");
  const appendix = summary.closest("details");
  expect(appendix).not.toHaveAttribute("open");
  fireEvent.click(summary);
  expect(screen.getAllByText("Calculation verified")[0]).toBeVisible();
  expect(screen.getByText("Display mismatched")).toBeVisible();
  expect(
    screen.getByText(
      "The calculation is valid, but the decision text or display scale does not match.",
    ),
  ).toBeVisible();
  expect(screen.queryByText("price / eps")).not.toBeVisible();
  fireEvent.click(screen.getByText("Formula, inputs, and Evidence"));
  expect(screen.getByText("price / eps")).toBeVisible();
  expect(screen.getByText("numeric.requirement.req_forward_pe.result_mismatch")).toBeVisible();
});

test("formats verified calculations before exposing formula audit fields", async () => {
  render(
    <Router initialPath="/runs/run-1?view=decision">
      <RunDetail />
    </Router>,
  );

  fireEvent.click(await screen.findByText("Decision-critical calculation audit"));
  const calculation = screen.getByText("Observed market anchor").closest("article");
  expect(calculation).not.toBeNull();
  expect(within(calculation!).getByText("Calculation verified")).toBeVisible();
  expect(within(calculation!).getByText("100 USD")).toBeVisible();
  expect(within(calculation!).getByText("2026-07-24")).toBeVisible();
  expect(within(calculation!).getByText("calc_market_reference")).not.toBeVisible();
  within(calculation!)
    .getAllByText("close", { exact: true })
    .forEach((value) => expect(value).not.toBeVisible());

  fireEvent.click(within(calculation!).getByText("Formula and Evidence"));
  expect(within(calculation!).getByText("calc_market_reference")).toBeVisible();
  within(calculation!)
    .getAllByText("close", { exact: true })
    .forEach((value) => expect(value).toBeVisible());
});

test("opens a locked Full clone template instead of rerunning immediately", async () => {
  render(
    <Router initialPath="/runs/run-1">
      <RunDetail />
      <LocationProbe />
    </Router>,
  );

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
    <Router initialPath="/runs/run-1?view=incremental">
      <RunDetail />
    </Router>,
  );

  expect(await screen.findByRole("heading", { name: "Executive summary" })).toBeVisible();
  expect(screen.getByText("Balanced research summary.")).toBeVisible();
  expect(screen.getByText(/Newly completed market session/)).toBeVisible();
  expect(screen.getByText("A complete refresh would resolve the scope gap.")).toBeVisible();
  expect(screen.getAllByText("Current instrument")[0]).toBeVisible();
  expect(screen.getAllByText("S&P 500")[0]).toBeVisible();
  expect(screen.getAllByText("NASDAQ 100")[0]).toBeVisible();
  expect(screen.getAllByText(/Reported benchmark difference/)).toHaveLength(2);
  fireEvent.click(screen.getByText("Decision-critical calculation audit"));
  expect(screen.getByText("Observed market anchor")).toBeVisible();
  expect(screen.getByText("calc_market_reference")).not.toBeVisible();
  expect(screen.getByRole("tab", { name: "Overview" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.queryByRole("tab", { name: "Reports" })).not.toBeInTheDocument();
  expect(screen.queryByRole("tab", { name: "Deliberation" })).not.toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "Analysis brief" })).toBeVisible();
  expect(screen.getByRole("tab", { name: "Reassessment" })).toBeVisible();
  expect(screen.getByRole("tab", { name: "Evidence updates" })).toBeVisible();
  expect(screen.getByRole("tab", { name: "Activity" })).toBeVisible();
  expect(screen.getByRole("link", { name: "Update this research" })).toHaveAttribute(
    "href",
    "/runs/new?intent=update&from_run=run-1&full_baseline_run_id=full-baseline",
  );

  await waitFor(() => expect(api.evidence).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole("tab", { name: "Analysis brief" }));
  const briefHeading = await screen.findByRole("heading", { name: "Key update" });
  expect(briefHeading).toBeVisible();
  expect(briefHeading.closest("article")).toHaveTextContent(
    "The filing changes the outlook.",
  );

  fireEvent.click(screen.getByRole("tab", { name: "Reassessment" }));
  expect(await screen.findByText("The new filing adds uncertainty.")).toBeVisible();
  expect(screen.getByText("1 changed · 1 total")).toBeVisible();
  const reaffirmedSummary = screen.getByText("Show 1 reaffirmed item");
  expect(reaffirmedSummary).toBeInTheDocument();
  expect(reaffirmedSummary.closest("details")).not.toHaveAttribute("open");
  const reassessmentPanel = screen
    .getByRole("heading", { name: "Reassessment" })
    .closest("article");
  expect(within(reassessmentPanel!).queryByText("Audit details")).toBeNull();
  expect(within(reassessmentPanel!).getByText("thesis", { exact: true })).not.toBeVisible();
  fireEvent.click(within(reassessmentPanel!).getByText("Technical mapping"));
  expect(within(reassessmentPanel!).getByText("thesis", { exact: true })).toBeVisible();
  expect(within(reassessmentPanel!).getByText("risks.0", { exact: true })).toBeVisible();
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
    <Router initialPath="/runs/run-1?view=evidence">
      <RunDetail />
    </Router>,
  );

  expect(await screen.findByRole("heading", { name: "Collection Summary" })).toBeVisible();
  expect(api.evidence).not.toHaveBeenCalled();
  expect(screen.getByText("Price snapshot · Composite snapshot")).toBeVisible();
  expect(screen.getByText("fixture.news")).toBeVisible();
  expect(screen.getAllByText("Fallback").length).toBeGreaterThan(0);
  const collectionSummary = screen
    .getByRole("heading", { name: "Collection Summary" })
    .closest("section");
  expect(within(collectionSummary!).getByText(/collection\.partial/)).not.toBeVisible();
  fireEvent.click(within(collectionSummary!).getByText("Collection diagnostics"));
  expect(screen.getByText(/collection\.partial/)).toBeVisible();

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
  fireEvent.click(screen.getByText("Canonical IDs and provenance"));
  expect(screen.getByText("ev_fedcba987654")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Close" }));

  fireEvent.click(screen.getByRole("tab", { name: "Analysis brief" }));
  expect(
    await screen.findByText("This historical run did not record an analysis brief."),
  ).toBeVisible();
  await waitFor(() => expect(api.evidence).toHaveBeenCalledWith("full-baseline"));
});

test.each([
  ["running", "The analysis brief is still being generated."],
  ["failed", "This run did not produce an analysis brief."],
  ["cancelled", "This run did not produce an analysis brief."],
] as const)("describes a missing Incremental brief for a %s run", async (status, message) => {
  const incremental = structuredClone(detail) as RunDetailType;
  incremental.run.research_kind = "incremental";
  incremental.run.status = status;
  incremental.result!.status = status;
  incremental.research_node = {
    id: "run-1",
    cycle_id: "full-baseline",
    instrument: "NVDA",
    analysis_date: "2026-07-24",
    research_schema_version: "1",
    information_cutoff_at: "2026-07-24T20:00:00Z",
    method_snapshot: {},
    research_kind: "incremental",
    full_baseline_run_id: "full-baseline",
    is_baseline_compatible: true,
    is_cycle_head: true,
    is_primary: true,
    is_active: true,
  } as ResearchNodeView;
  incremental.incremental_context = {
    analysis_brief: null,
    full_baseline: {
      run_id: "full-baseline",
      analysis_date: "2026-07-20",
      decision: detail.result!.decision!,
    },
  };
  vi.mocked(api.run).mockResolvedValue(incremental);

  render(
    <Router initialPath="/runs/run-1?view=brief">
      <RunDetail />
    </Router>,
  );

  expect(await screen.findByText(message)).toBeVisible();
});

test("streams through historical failed attempts before closing on the current success", async () => {
  const retried = structuredClone(detail) as RunDetailType;
  retried.run.attempt = 4;
  retried.run.status = "succeeded";
  vi.mocked(api.run).mockResolvedValue(retried);

  render(
    <Router initialPath="/runs/run-1?view=timeline">
      <RunDetail />
    </Router>,
  );
  await screen.findByRole("heading", { name: "Activity" });
  const stream = FakeEventSource.instance;

  await act(async () => {
    stream.emit("run.failed", {
      run_id: "run-1",
      sequence: 11,
      attempt: 1,
      event_type: "run.failed",
      node: null,
      payload: {},
      created_at: "2026-08-27T09:59:17Z",
    });
  });
  expect(stream.closed).toBe(false);

  await act(async () => {
    stream.emit("run.succeeded", {
      run_id: "run-1",
      sequence: 47,
      attempt: 4,
      event_type: "run.succeeded",
      node: null,
      payload: {},
      created_at: "2026-08-27T15:08:50Z",
    });
  });
  expect(stream.closed).toBe(true);
});

test("opens a fresh event stream from the last sequence after retry", async () => {
  const failed = structuredClone(detail) as RunDetailType;
  failed.run.status = "failed";
  failed.run.attempt = 1;
  failed.result!.status = "failed";
  const queued = structuredClone(failed) as RunDetailType;
  queued.run.status = "queued";
  queued.run.attempt = 2;
  queued.result!.status = "queued";
  let current = failed;
  vi.mocked(api.run).mockImplementation(async () => current);
  vi.mocked(api.action).mockImplementation(async () => {
    current = queued;
    return queued.run;
  });

  render(
    <Router initialPath="/runs/run-1?view=timeline">
      <RunDetail />
    </Router>,
  );
  await screen.findByRole("heading", { name: "Activity" });
  const first = FakeEventSource.instance;
  act(() => first.emit("run.failed", {
    run_id: "run-1",
    sequence: 11,
    attempt: 1,
    event_type: "run.failed",
    node: null,
    payload: {},
    created_at: "2026-08-27T09:59:17Z",
  }));
  expect(first.closed).toBe(true);

  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(2));
  expect(FakeEventSource.instance.url).toBe("/api/v1/runs/run-1/events?after=11");
});

function performanceCalculation(unroundedReturn: number) {
  return {
    provider: "fixture-feed",
    fallback: false,
    adjustment_basis: "split-adjusted close",
    retrieved_at: "2026-07-24T20:05:00Z",
    baseline_information_cutoff_at: "2026-07-20T20:00:00Z",
    target_information_cutoff_at: "2026-07-24T20:00:00Z",
    start_session: "2026-07-20",
    end_session: "2026-07-24",
    start_value: 100,
    end_value: 112,
    formula: "(end / start) - 1",
    unrounded_return: unroundedReturn,
  };
}

function incrementalDetailWithPerformanceReason(reason: string): RunDetailType {
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
    method_snapshot: {},
    decision: incremental.result!.decision,
    is_active: true,
    is_primary: true,
    is_cycle_head: true,
    cycle_warning: false,
    collection_summary: { domains: [] },
    research_availability: { domains: [] },
    information_advancement: { advanced: false, reasons: [] },
    performance: {
      stock: { status: "unavailable", reason },
      benchmarks: [],
    },
    reassessment: { entries: [] },
    full_research_required_reasons: [],
  } as never;
  return incremental;
}

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
      <Router initialPath="/runs/run-1">
        <RunDetail />
      </Router>,
    );

    expect(await screen.findByText(fallback)).toBeVisible();
    expect(screen.queryByText(unknownReason)).not.toBeInTheDocument();
  },
);

test("loads only Run Detail initially and refreshes open deliberation artifacts on SSE", async () => {
  render(
    <Router initialPath="/runs/run-1">
      <RunDetail />
    </Router>,
  );

  expect(await screen.findByRole("tab", { name: "Overview" })).toBeVisible();
  expect(api.capabilities).not.toHaveBeenCalled();
  expect(api.artifacts).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("tab", { name: "Deliberation" }));
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

test("localizes Incremental activity and keeps one technical event log per attempt", async () => {
  const incremental = structuredClone(detail) as RunDetailType;
  incremental.run.research_kind = "incremental";
  incremental.run.full_baseline_run_id = "full-baseline";
  incremental.run.is_research_node = true;
  incremental.research_node = {
    id: "run-1",
    cycle_id: "full-baseline",
    instrument: "NVDA",
    analysis_date: "2026-07-24",
    research_kind: "incremental",
    full_baseline_run_id: "full-baseline",
    research_schema_version: "1",
    information_cutoff_at: "2026-07-24T20:00:00Z",
    method_snapshot: {},
    is_baseline_compatible: false,
    is_active: true,
    is_primary: true,
    is_cycle_head: true,
    cycle_warning: false,
    full_research_required_reasons: [],
  } as ResearchNodeView;
  vi.mocked(api.run).mockResolvedValue(incremental);

  render(
    <Router initialPath="/runs/run-1?view=timeline">
      <RunDetail />
    </Router>,
  );
  await screen.findByRole("tab", { name: "Activity" });

  act(() =>
    FakeEventSource.instance.emit("incremental.collection_completed", {
      run_id: "run-1",
      sequence: 9,
      attempt: 1,
      event_type: "incremental.collection_completed",
      node: "incremental.collect",
      payload: { domains: 4 },
      created_at: "2026-07-24T00:01:20Z",
    } as RunEvent),
  );
  act(() =>
    FakeEventSource.instance.emit("node.output_retry", {
      run_id: "run-1",
      sequence: 10,
      attempt: 1,
      event_type: "node.output_retry",
      node: "incremental.synthesis.serialize",
      payload: { reason_code: "schema_validation" },
      created_at: "2026-07-24T00:01:21Z",
    } as RunEvent),
  );

  const activityTitle = await screen.findByText("Collection · Collection update");
  const workUnit = activityTitle.closest("article");
  expect(within(workUnit!).getByText("Completed")).toBeVisible();
  expect(within(workUnit!).getByText("incremental.collect")).toBeVisible();
  expect(screen.getByText("schema_validation")).toBeVisible();
  const attempt = screen.getByText("Attempt 1").closest("details");
  expect(within(attempt!).queryByText("Audit details")).not.toBeInTheDocument();
  const technicalEvents = within(attempt!).getByText("Technical events (2)");
  expect(screen.queryByText(/incremental\.collection_completed/)).not.toBeVisible();
  fireEvent.click(technicalEvents);
  expect(screen.getByText(/incremental\.collection_completed/)).toBeVisible();
  expect(screen.getAllByText(/incremental\.collect/).some((element) => element.matches("code"))).toBe(true);
});

test("orders work units within each attempt and restores the activity preference", async () => {
  localStorage.setItem("tradingagents-timeline-order", "oldest");
  render(
    <Router initialPath="/runs/run-1?view=timeline">
      <RunDetail />
    </Router>,
  );
  await screen.findByRole("tab", { name: "Activity" });

  act(() => {
    FakeEventSource.instance.emit("node.completed", {
      run_id: "run-1",
      sequence: 9,
      attempt: 1,
      event_type: "node.completed",
      node: "analyst.news.report",
      payload: {},
      created_at: "2026-07-24T00:01:20Z",
    } as RunEvent);
    FakeEventSource.instance.emit("node.completed", {
      run_id: "run-1",
      sequence: 10,
      attempt: 1,
      event_type: "node.completed",
      node: "risk.review",
      payload: {},
      created_at: "2026-07-24T00:01:21Z",
    } as RunEvent);
  });

  const attempt = screen.getByText("Attempt 1").closest("details");
  expect(attempt).not.toBeNull();
  const nodes = () =>
    within(attempt!).getAllByText(/^(analyst\.news\.report|risk\.review)$/)
      .map((element) => element.textContent);

  expect(screen.getByRole("button", { name: "Earliest first" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(nodes()).toEqual(["analyst.news.report", "risk.review"]);

  fireEvent.click(within(attempt!).getByText("Technical events (2)"));
  const rawEventSequences = () =>
    within(attempt!)
      .getAllByText(/"sequence":(?:9|10)/)
      .map((element) => element.textContent);
  expect(rawEventSequences()[0]).toContain('"sequence":9');

  fireEvent.click(screen.getByRole("button", { name: "Latest first" }));
  expect(nodes()).toEqual(["risk.review", "analyst.news.report"]);
  expect(rawEventSequences()[0]).toContain('"sequence":9');
  expect(localStorage.getItem("tradingagents-timeline-order")).toBe("newest");
});

test("shows run metrics only in the Activity view", async () => {
  render(
    <Router initialPath="/runs/run-1">
      <RunDetail />
    </Router>,
  );

  await screen.findByRole("tab", { name: "Overview" });
  expect(screen.queryByText("Attempt metrics")).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
  const metricsSummary = await screen.findByText("Run metrics and diagnostics");
  expect(screen.getByText("4 LLM calls · 1,200 input · 400 output · 12.4s")).toBeVisible();
  expect(screen.getByText("Attempt metrics")).not.toBeVisible();
  fireEvent.click(metricsSummary);
  expect(await screen.findByText("Attempt metrics")).toBeVisible();
});

test("shows trashed retention details and restores without deleting data", async () => {
  vi.mocked(api.run)
    .mockResolvedValueOnce({
      ...detail,
      run: {
        ...detail.run,
        research_schema_version: "1",
        is_research_node: false,
        trashed_at: "2026-07-01T00:00:00Z",
      },
    } as RunDetailType)
    .mockResolvedValue({
      ...detail,
      run: { ...detail.run, trashed_at: null },
    } as RunDetailType);
  vi.mocked(api.restoreRuns).mockResolvedValue({
    runs: [],
    changed: 1,
  });

  render(
    <Router initialPath="/runs/run-1">
      <RunDetail />
    </Router>,
  );

  expect(await screen.findByText("NVIDIA Corporation")).toBeVisible();
  expect(screen.getByText("This run is in Trash")).toBeVisible();
  expect(screen.getByText(/Scheduled permanent deletion/)).toBeVisible();
  expect(screen.getByText(/day|due/)).toBeVisible();
  expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Restore" }));
  await waitFor(() =>
    expect(api.restoreRuns).toHaveBeenCalledWith(["run-1"]),
  );
});

test("always enters report audit details collapsed", async () => {
  localStorage.setItem("tradingagents-audit-details-open", "true");
  const unavailableEvidence = structuredClone(detail);
  unavailableEvidence.result!.evidence!.items[0].quality = "unavailable";
  vi.mocked(api.run).mockResolvedValue(unavailableEvidence);
  const view = render(
    <Router initialPath="/runs/run-1?view=reports&report=market">
      <RunDetail />
    </Router>,
  );

  await screen.findByRole("heading", { name: "Market report" });
  expect(screen.getByText("Historical source was partial.")).not.toBeVisible();
  await act(async () => {
    fireEvent.click(screen.getByText("Audit details"));
  });
  expect(screen.getByText("Historical source was partial.")).toBeVisible();
  expect(view.container.querySelector(".audit-evidence-grid")).toBeVisible();
  expect(view.container.querySelector(".audit-source-name")).toHaveTextContent(
    "fixture, alternate-fixture",
  );
  expect(
    view.container.querySelector(".audit-evidence-grid .quality"),
  ).toHaveTextContent("Unavailable");
  fireEvent.click(screen.getByRole("button", { name: "News" }));
  fireEvent.click(screen.getByRole("button", { name: "Market" }));
  expect(screen.getByText("Historical source was partial.")).not.toBeVisible();
  expect(localStorage.getItem("tradingagents-audit-details-open")).toBe("true");
});

test("labels runs that have no recorded artifacts", async () => {
  vi.mocked(api.artifacts).mockResolvedValue([]);
  render(
    <Router initialPath="/runs/run-1">
      <RunDetail />
    </Router>,
  );

  expect(await screen.findByRole("heading", { name: "NVIDIA Corporation" })).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "Deliberation" }));

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
    <Router initialPath="/runs/run-1">
      <RunDetail />
    </Router>,
  );

  expect(await screen.findByRole("heading", { name: "NVIDIA Corporation" })).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "Activity" }));
  fireEvent.click(await screen.findByText("Run metrics and diagnostics"));
  const roleMetricsTitle = screen.getByText("Metrics by role");
  const roleMetrics = roleMetricsTitle.closest("details");
  expect(roleMetrics).not.toHaveAttribute("open");

  fireEvent.click(roleMetricsTitle);

  expect(roleMetrics).toHaveAttribute("open");
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
  expect(committee).toHaveTextContent("0/1");
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

  const attemptSummary = screen.getByText("Attempt metrics", { exact: false });
  const attemptDetails = attemptSummary.closest("details");
  expect(
    attemptSummary
      .closest("summary")!
      .querySelector(".metric-disclosure-arrow"),
  ).toHaveTextContent("›");
  expect(attemptDetails).not.toHaveAttribute("open");
  fireEvent.click(attemptSummary);
  expect(attemptDetails).toHaveAttribute("open");
  const attemptRow = within(attemptDetails!).getByText(
    "Succeeded",
  ).closest("tr");
  expect(attemptRow).toHaveTextContent("Succeeded");
  expect(attemptRow).toHaveTextContent("1,200");
  expect(attemptRow).toHaveTextContent("12.4s");
});

test("shows persisted run metrics when a failed run has no result", async () => {
  vi.mocked(api.run).mockResolvedValue({
    ...detail,
    run: {
      ...detail.run,
      status: "failed",
      error_code: "StructuredOutputError",
      error_message: "Validated output failed.",
    },
    result: null,
    attempts: [
      {
        ...detail.attempts![0],
        status: "failed",
        error_code: "StructuredOutputError",
      },
    ],
  } as RunDetailType);

  render(
    <Router initialPath="/runs/run-1">
      <RunDetail />
    </Router>,
  );

  expect(await screen.findByText("Validated output failed.")).toBeVisible();
  fireEvent.click(screen.getByText("Run metrics and diagnostics"));
  expect(screen.getAllByText("1,200")[0]).toBeVisible();
  fireEvent.click(screen.getByText("Attempt metrics", { exact: false }));
  expect(screen.getByText("StructuredOutputError")).toBeVisible();
});

test("loads sealed evidence immediately when the SSE seal event arrives", async () => {
  const runningPending = {
    ...detail,
    run: { ...detail.run, status: "running" },
    result: {
      ...detail.result,
      status: "running",
      reports: {},
      decision: null,
      evidence: null,
    },
    evidence_status: {
      status: "pending",
      digest: null,
      item_count: 0,
      table_count: 0,
      sealed_attempt: null,
      sealed_at: null,
    },
  } as RunDetailType;
  const runningSealed = {
    ...runningPending,
    result: {
      ...runningPending.result,
      evidence: detail.result!.evidence,
    },
    evidence_status: detail.evidence_status,
  } as RunDetailType;
  vi.mocked(api.run)
    .mockResolvedValueOnce(runningPending)
    .mockResolvedValue(runningSealed);
  vi.mocked(api.artifacts).mockResolvedValue([]);

  render(
    <Router initialPath="/runs/run-1?view=evidence">
      <RunDetail />
    </Router>,
  );

  expect(await screen.findByText(/Evidence collection is still/)).toBeVisible();
  expect(api.evidence).not.toHaveBeenCalled();

  act(() => {
    FakeEventSource.instance.emit("evidence.sealed", {
      run_id: "run-1",
      sequence: 5,
      attempt: 1,
      event_type: "evidence.sealed",
      node: "evidence.seal",
      payload: {
        digest: "fixture-digest",
        item_count: 2,
        table_count: 0,
      },
      created_at: "2026-07-24T00:00:30Z",
    });
  });

  expect(
    await screen.findByRole("heading", {
      name: "Price snapshot · Composite snapshot",
    }),
  ).toBeVisible();
  expect(api.evidence).not.toHaveBeenCalled();
});

test("shows preserved decision artifacts after opening deliberation for an unsuccessful run", async () => {
  const decisionArtifact = {
    id: "artifact-decision",
    run_id: "run-1",
    attempt: 1,
    stage: "decision",
    role: "final_committee",
    round: 0,
    schema_version: "2",
    prompt_version: "final-committee-v4-split",
    generation_method: "tool_call",
    created_at: "2026-07-24T00:00:55Z",
    content: detail.result!.decision!,
  } as ResearchArtifact;
  vi.mocked(api.run).mockResolvedValue({
    ...detail,
    run: {
      ...detail.run,
      status: "failed",
      error_code: "PersistenceError",
      error_message: "Completion failed after the decision was saved.",
    },
    result: {
      ...detail.result,
      status: "failed",
      decision: null,
    },
  } as RunDetailType);
  vi.mocked(api.artifacts).mockResolvedValue([
    ...artifacts,
    decisionArtifact,
  ]);

  render(
    <Router initialPath="/runs/run-1?view=deliberation">
      <RunDetail />
    </Router>,
  );

  await screen.findByRole("heading", { name: "Deliberation" });
  fireEvent.click(screen.getByRole("tab", { name: "Overview" }));
  expect(await screen.findByText("Evidence is balanced.")).toBeVisible();
  expect(screen.getByText(/Partial research is available/)).toBeVisible();
});

test("keeps report footnote navigation in an in-page source drawer", async () => {
  const initialPath = "/runs/run-1?view=reports&report=news";
  const view = render(
    <Router initialPath={initialPath}>
      <RunDetail />
      <LocationProbe />
    </Router>,
  );

  expect(
    await screen.findByRole("heading", { name: "News report" }),
  ).toBeVisible();
  fireEvent.click(
    screen.getAllByRole("button", {
      name: "Open evidence ev_0123456789ab",
    })[0],
  );
  expect(screen.getByRole("dialog", { name: "Source details" })).toBeVisible();
  const provenance = screen.getByText("Canonical IDs and provenance");
  expect(provenance.closest("details")).not.toHaveAttribute("open");
  fireEvent.click(provenance);
  expect(provenance.closest("details")).toHaveAttribute("open");
  expect(screen.getByTestId("router-location")).toHaveTextContent(
    "/runs/run-1?view=reports&report=news",
  );
  fireEvent.click(screen.getByRole("button", { name: "Close" }));
  expect(
    await screen.findByRole("heading", { name: "News report" }),
  ).toBeVisible();
  fireEvent.click(
    screen.getAllByRole("button", {
      name: "Open evidence ev_0123456789ab",
    })[0],
  );
  expect(
    screen.getByText("Canonical IDs and provenance").closest("details"),
  ).not.toHaveAttribute("open");
  fireEvent.click(screen.getByRole("button", { name: "Close" }));

  const restoredPath =
    screen.getByTestId("router-location").textContent ?? initialPath;
  view.unmount();
  render(
    <Router initialPath={restoredPath}>
      <RunDetail />
    </Router>,
  );

  expect(
    await screen.findByRole("heading", { name: "News report" }),
  ).toBeVisible();
});

test("localizes canonical report labels for zh-CN", async () => {
  await act(() => i18n.changeLanguage("zh-CN"));
  render(
    <Router initialPath="/runs/run-1?view=reports">
      <RunDetail />
    </Router>,
  );

  await waitFor(() => {
    expect(
      screen.getByRole("heading", { name: "Fundamentals report" }),
    ).toBeVisible();
  });
  const labels = ["基本面", "市场", "新闻", "舆情"].map((name) =>
    screen.getByRole("button", { name }),
  );
  labels.slice(0, -1).forEach((label, index) => {
    expect(
      label.compareDocumentPosition(labels[index + 1]) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).not.toBe(0);
  });
});
