import { beforeEach,vi } from "vitest";

import { useLocation } from "../../app/router";
import {
api,
type Capabilities,
type ResearchArtifact,
type RunDetail as RunDetailType,
type RunEvent
} from "../../shared/api/client";
import i18n from "../../shared/i18n";
import RunDetail from "../runs/RunDetail";
import ResearchReader from "./ResearchReader";

function ReaderFixture() {
  const location = useLocation();
  return location.pathname.startsWith("/runs/") ? <RunDetail /> : <ResearchReader selectedRunId={new URLSearchParams(location.search).get("node") ?? "run-1"} />;
}


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
    is_research_node: true,
    research_kind: "full",
    research_schema_version: "2",
    request: {
      ticker: "NVDA",
      analysis_date: "2026-07-24",
      profile: "standard",
      analysts: ["market"],
      models: {quick: {connection_id: "default", model: "gpt-5.4-mini", reasoning_effort: "provider_default"}, deep: {connection_id: "default", model: "gpt-5.5", reasoning_effort: "provider_default"}},
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
          temporal_basis: "live_snapshot",
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
  vi.mocked(api.analysisCutoffContext).mockResolvedValue({ max_analysis_date: "2026-07-26", observed_at: "2026-07-26T00:00:00Z", valid_until: "2999-01-01T00:00:00Z" } as never);
  vi.mocked(api.run).mockResolvedValue(detail);
  vi.mocked(api.evidence).mockResolvedValue(detail.result!.evidence!);
  vi.mocked(api.artifacts).mockResolvedValue(artifacts);
  vi.mocked(api.capabilities).mockResolvedValue({
    defaults: { trash_retention_days: 30 },
  } as Capabilities);
  vi.stubGlobal("EventSource", FakeEventSource);
});
export { analystReport,artifacts,detail,FakeEventSource,LocationProbe,ReaderFixture,runMetrics };

export function performanceCalculation(unroundedReturn: number) {
  return {
    provider: "fixture-feed",
    fallback: false,
    adjustment_basis: "split-adjusted close",
    retrieved_at: "2026-07-24T20:05:00Z",
    baseline_information_cutoff_at: "2026-07-20T20:00:00Z",
    target_information_cutoff_at: "2026-07-24T20:00:00Z",
    start_session: "2026-07-20",
    end_session: "2026-07-24",
    start_value: 100.123456,
    end_value: 112.987654,
    formula: "(end / start) - 1",
    unrounded_return: unroundedReturn,
  };
}

export function incrementalDetailWithPerformanceReason(reason: string): RunDetailType {
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
    decision_outcome: "unchanged",
    decision_outcome_reason: "The baseline Decision remains valid as written.",
    full_research_required_reasons: [],
  } as never;
  return incremental;
}
