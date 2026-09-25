import { models } from "./models";

export const timestamp = "2026-07-24T00:00:00Z";

export type MockRun = ReturnType<typeof makeRun>;

export function makeRun(
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
    research_schema_version: "2",
    research_kind: "full",
    is_research_node: status === "succeeded",
    information_cutoff_at: status === "succeeded" ? "2026-07-24T23:59:59Z" : null,
    method_snapshot: status === "succeeded" ? { llm_provider: "openai" } : null,
    research_rating: status === "succeeded" ? "Hold" : null,
    trashed_at: options.trashedAt ?? null,
    status,
    request: {
      ticker: options.ticker ?? "NVDA",
      analysis_date: "2026-07-24",
      profile: "standard",
      analysts: ["market", "news"],
      models,
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

export function result(id: string) {
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
      confidence: "medium",
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
      market_reference_levels: [],
      risk_review_adjustments: [],
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

export function artifacts(id: string) {
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

export type TimelineNodeFixture = {
  id: string;
  cycle_id: string;
  research_kind: "full" | "incremental";
  [key: string]: unknown;
};

export function cycleTimeline(
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
