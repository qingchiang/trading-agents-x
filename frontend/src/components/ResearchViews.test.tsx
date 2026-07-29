import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import type {
  AnalystReport,
  EvidenceBundle,
  ResearchArtifact,
  ResearchDecision,
} from "../api/client";
import { buildEvidenceReferenceIndex } from "../evidence";
import i18n from "../i18n";
import AnalystReportView from "./AnalystReportView";
import DeliberationView from "./DeliberationView";
import ResearchDecisionView from "./ResearchDecisionView";

const firstRef = "ev_0123456789ab";
const secondRef = "ev_111111111111";

const evidence = {
  version: "3",
  instrument: "NVDA",
  analysis_date: "2026-07-24",
  sealed_at: "2026-07-24T00:00:00Z",
  digest: "fixture",
  items: [
    {
      ref: firstRef,
      source: "fixture",
      evidence_type: "market",
      requested_date: "2026-07-24",
      content: "First source body",
      quality: "high",
      fallback: false,
    },
    {
      ref: secondRef,
      source: "fixture",
      evidence_type: "fundamentals",
      requested_date: "2026-07-24",
      content: "Second source body",
      quality: "high",
      fallback: false,
    },
  ],
  tables: [
    {
      id: "et_0123456789ab",
      title: "Complete evidence snapshot",
      purpose: "Let the reader independently inspect every period.",
      source_format: "structured",
      evidence_refs: [firstRef],
      columns: [
        { key: "period", label: "Period", data_type: "date" },
        { key: "value", label: "Value", data_type: "number", unit: "USD" },
      ],
      rows: Array.from({ length: 14 }, (_, index) => ({
        id: `row.${index + 1}`,
        cells: {
          period: {
            raw_value: `2026-${String(index + 1).padStart(2, "0")}`,
            display_value: `Period ${index + 1}`,
            kind: "observation",
            evidence_refs: [firstRef],
          },
          value: {
            raw_value: index + 1,
            display_value: `Value ${index + 1}`,
            kind: "observation",
            evidence_refs: [firstRef],
          },
        },
      })),
    },
  ],
} as unknown as EvidenceBundle;

const report = {
  analyst: "market",
  executive_summary: "Executive evidence summary.",
  confidence: 0.78,
  claims: [
    {
      id: "market.claim_1",
      kind: "inference",
      statement: `The observed trend is constructive ${firstRef}.`,
      implication: "The committee should preserve upside sensitivity.",
      confidence: 0.72,
      evidence_refs: [firstRef],
    },
  ],
  sections: [
    {
      id: "snapshot",
      title: "Snapshot section",
      narrative: "First narrative.",
      table_ids: ["et_0123456789ab"],
    },
    {
      id: "comparison",
      title: "Comparison section",
      narrative: "Second narrative.",
      table_ids: ["rt_market_comparison"],
    },
  ],
  tables: [
    {
      id: "rt_market_comparison",
      title: "AI comparison",
      purpose: "Explain the evidence rather than duplicate it.",
      source_table_id: "et_0123456789ab",
      total_source_rows: 14,
      source_row_ids: ["row.1", "row.2"],
      columns: [
        { key: "period", label: "Period", data_type: "text" },
        { key: "change", label: "Change", data_type: "number", unit: "%" },
      ],
      rows: [
        {
          id: "comparison.1",
          cells: {
            period: {
              raw_value: "Period 1",
              display_value: "Period 1",
              kind: "inference",
              evidence_refs: [firstRef],
            },
            change: {
              raw_value: 10,
              display_value: "10%",
              kind: "derived",
              evidence_refs: [firstRef, secondRef],
              derived: {
                formula: "(latest / prior - 1) * 100",
                inputs: { latest: 110, prior: 100 },
                input_evidence_refs: [firstRef, secondRef],
                result: 10,
                unit: "%",
              },
            },
          },
        },
        {
          id: "comparison.2",
          cells: {
            period: {
              raw_value: "Period 2",
              display_value: "Period 2",
              kind: "inference",
              evidence_refs: [firstRef],
            },
            change: {
              raw_value: 5,
              display_value: "5%",
              kind: "inference",
              evidence_refs: [secondRef],
            },
          },
        },
      ],
    },
  ],
  catalysts: ["Demand accelerates."],
  risks: ["Demand slows."],
  invalidation_conditions: ["The observed trend reverses."],
  evidence_refs: [firstRef, secondRef],
  warnings: [],
} as unknown as AnalystReport;

const decision = {
  rating: "Overweight",
  confidence: 0.74,
  executive_summary: "The evidence supports a conditional positive view.",
  thesis: "Operating leverage can improve if demand remains durable.",
  evidence_refs: [firstRef, secondRef],
  memory_refs: ["memory:prior-run"],
  catalysts: ["Demand accelerates."],
  risks: ["Demand normalizes faster than expected."],
  invalidation_conditions: ["Cash conversion deteriorates."],
  unresolved_questions: ["How durable is the next replacement cycle?"],
  time_horizon: "6-12 months",
  scenarios: [
    {
      kind: "bear",
      core_assumptions: ["Demand contracts."],
      outcome: "Margins compress.",
      evidence_refs: [secondRef],
      valuation_range: { low: 80, high: 95 },
    },
    {
      kind: "base",
      core_assumptions: ["Demand remains stable."],
      outcome: "Earnings compound moderately.",
      evidence_refs: [firstRef],
      valuation_range: { low: 100, high: 120 },
    },
    {
      kind: "bull",
      core_assumptions: ["Demand accelerates."],
      outcome: "Operating leverage expands.",
      evidence_refs: [firstRef, secondRef],
      valuation_range: { low: 130, high: 155 },
    },
  ],
  valuation_assessment: {
    method: "Scenario-weighted earnings multiple",
    valuation_range: { low: 100, high: 130 },
    currency: "USD",
    as_of_date: "2026-07-24",
    input_evidence_refs: [firstRef, secondRef],
    limitations: ["The multiple is sensitive to cycle duration."],
  },
  market_reference_levels: [
    {
      level_type: "recent_support",
      value: 98,
      unit: "USD",
      as_of_date: "2026-07-24",
      interpretation: "A market reference, not a mandatory entry.",
      evidence_refs: [firstRef],
    },
  ],
  risk_review_adjustments: [
    {
      source_role: "conservative",
      disposition: "modified",
      subject: "Confidence calibration",
      explanation: "Confidence was reduced because coverage is incomplete.",
      evidence_refs: [secondRef],
    },
  ],
} as unknown as ResearchDecision;

const evidenceIndex = buildEvidenceReferenceIndex(evidence);

beforeEach(async () => {
  await i18n.changeLanguage("en");
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
  });
});

test("interleaves complete typed tables with report sections", () => {
  const onEvidence = vi.fn();
  render(
    <AnalystReportView
      report={report}
      evidence={evidence}
      evidenceIndex={evidenceIndex}
      onEvidence={onEvidence}
    />,
  );

  const firstNarrative = screen.getByText("First narrative.");
  const evidenceTitle = screen.getByRole("heading", {
    name: "Complete evidence snapshot",
  });
  const secondNarrative = screen.getByText("Second narrative.");
  const researchTitle = screen.getByRole("heading", { name: "AI comparison" });
  expect(
    firstNarrative.compareDocumentPosition(evidenceTitle) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).not.toBe(0);
  expect(
    evidenceTitle.compareDocumentPosition(secondNarrative) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).not.toBe(0);
  expect(
    secondNarrative.compareDocumentPosition(researchTitle) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).not.toBe(0);
  expect(screen.getByText("Evidence data table")).toBeVisible();
  expect(screen.getByText("AI research table")).toBeVisible();
  expect(
    screen.getByText("Current report view: 2/14 source rows."),
  ).toBeVisible();
  expect(screen.getByText("Derived value details")).toBeVisible();

  const evidenceCard = evidenceTitle.closest("section");
  expect(evidenceCard).not.toBeNull();
  expect(within(evidenceCard!).queryByText("Value 14")).not.toBeInTheDocument();
  fireEvent.click(
    within(evidenceCard!).getByRole("button", { name: "Expand all rows" }),
  );
  expect(within(evidenceCard!).getByText("Value 14")).toBeVisible();

  fireEvent.click(
    screen.getByRole("button", { name: "Open complete evidence table" }),
  );
  expect(screen.getByText("Complete evidence table")).toBeVisible();
  fireEvent.click(
    screen.getAllByRole("button", {
      name: `Open evidence ${firstRef}`,
    })[0],
  );
  expect(onEvidence).toHaveBeenCalledWith(firstRef);
});

test("groups the complete claim-driven deliberation by research stage", () => {
  const artifacts = deliberationArtifacts();
  render(
    <DeliberationView
      artifacts={artifacts}
      evidenceIndex={evidenceIndex}
      onEvidence={vi.fn()}
    />,
  );

  expect(screen.getByRole("heading", { name: "Bull and bear cases" })).toBeVisible();
  expect(screen.getByText("Bull case")).toBeVisible();
  expect(screen.getByText("Bear case")).toBeVisible();
  expect(screen.getByText("Bull position text.")).toBeVisible();
  expect(screen.getByText("Bear position text.")).toBeVisible();
  expect(screen.getByRole("heading", { name: "Rebuttal round 1" })).toBeVisible();
  expect(screen.getAllByText("Demand transmits through volume.")).toHaveLength(4);
  expect(screen.getByText(/Current resolution:/)).toHaveTextContent("mixed");
  expect(screen.getAllByText("Reduce confidence.")).toHaveLength(3);
  expect(screen.getByRole("heading", { name: "Final research opinion" })).toBeVisible();
  expect(
    screen.getByText(
      "Non-personalized research opinion — not an account-level instruction",
    ),
  ).toBeVisible();
  expect(
    screen.getAllByText(
      (_content, element) =>
        element?.tagName === "SMALL" &&
        Boolean(element.textContent?.includes("research-v2")),
    ).length,
  ).toBeGreaterThan(0);
});

test("renders scenarios, valuation, reference levels, and risk dispositions", () => {
  render(
    <ResearchDecisionView
      decision={decision}
      evidenceIndex={evidenceIndex}
      onEvidence={vi.fn()}
    />,
  );

  expect(screen.getByText("Overweight")).toBeVisible();
  const base = screen.getByText("Base scenario");
  const bull = screen.getByText("Bull scenario");
  const bear = screen.getByText("Bear scenario");
  expect(
    base.compareDocumentPosition(bull) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).not.toBe(0);
  expect(
    bull.compareDocumentPosition(bear) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).not.toBe(0);
  expect(screen.getByText("100–130 USD")).toBeVisible();
  expect(screen.getByText("recent support")).toBeVisible();
  expect(
    screen.getByText(
      "Reference observations only; these are not mandatory entry, stop, or take-profit instructions.",
    ),
  ).toBeVisible();
  expect(screen.getByText("Confidence calibration")).toBeVisible();
  expect(screen.getByText("Modified")).toBeVisible();
  expect(screen.getByText("How durable is the next replacement cycle?")).toBeVisible();
});

function deliberationArtifacts(): ResearchArtifact[] {
  const base = {
    run_id: "run-1",
    attempt: 1,
    schema_version: "1",
    prompt_version: "research-v2",
    generation_method: "tool_call",
    created_at: "2026-07-24T00:00:00Z",
  };
  const researchCase = (role: "bull" | "bear") => ({
    role,
    executive_summary: `${role} summary.`,
    thesis: `${role} thesis.`,
    arguments: [
      {
        id: `case.${role}.argument_1`,
        claim_ids: ["market.claim_1"],
        statement: `${role} argument.`,
        mechanism: "Demand transmits through volume.",
        implication: "Earnings sensitivity changes.",
        confidence: 0.65,
        evidence_refs: [firstRef],
      },
    ],
    strongest_counterarguments: ["The opposing case remains plausible."],
    fragile_assumptions: ["Demand remains durable."],
    catalysts: [],
    risks: ["Demand may slow."],
    evidence_refs: [firstRef],
  });
  const rebuttal = (role: "bull" | "bear") => ({
    role,
    round: 1,
    thesis_update: `${role} updated thesis.`,
    responses: [
      {
        agenda_id: "debate.issue_1",
        claim_ids: ["market.claim_1"],
        response: `${role} targeted response.`,
        causal_mechanism: "Demand transmits through volume.",
        outcome: "unresolved",
        evidence_refs: [firstRef],
        new_evidence_refs: [],
        remaining_questions: ["Which mechanism dominates?"],
      },
    ],
    evidence_refs: [firstRef],
    new_evidence_refs: [],
    remaining_questions: ["Which mechanism dominates?"],
  });
  return [
    {
      ...base,
      id: "bull",
      stage: "case",
      role: "bull",
      round: 0,
      content: researchCase("bull"),
    },
    {
      ...base,
      id: "bear",
      stage: "case",
      role: "bear",
      round: 0,
      content: researchCase("bear"),
    },
    {
      ...base,
      id: "agenda",
      stage: "agenda",
      role: "moderator",
      round: 0,
      content: {
        executive_summary: "Material debate agenda.",
        issues: [
          {
            id: "debate.issue_1",
            question: "Which mechanism dominates?",
            claim_ids: ["market.claim_1"],
            importance: "material",
            bull_position: "Bull position text.",
            bear_position: "Bear position text.",
            evidence_refs: [firstRef],
          },
        ],
        evidence_refs: [firstRef],
      },
    },
    {
      ...base,
      id: "bull-rebuttal",
      stage: "rebuttal",
      role: "bull",
      round: 1,
      content: rebuttal("bull"),
    },
    {
      ...base,
      id: "bear-rebuttal",
      stage: "rebuttal",
      role: "bear",
      round: 1,
      content: rebuttal("bear"),
    },
    {
      ...base,
      id: "judge",
      stage: "judge",
      role: "research_judge",
      round: 0,
      content: {
        preliminary_rating: "Hold",
        confidence: 0.62,
        executive_summary: "Judge summary.",
        thesis: "Judge thesis.",
        rulings: [
          {
            agenda_id: "debate.issue_1",
            resolution: "mixed",
            rationale: "Both mechanisms retain support.",
            accepted_claim_ids: ["market.claim_1"],
            rejected_claim_ids: [],
            evidence_refs: [firstRef],
          },
        ],
        catalysts: [],
        risks: ["Demand slows."],
        invalidation_conditions: ["Demand evidence reverses."],
        unresolved_questions: ["Which mechanism dominates?"],
        time_horizon: "6-12 months",
        evidence_refs: [firstRef],
        memory_refs: [],
      },
    },
    ...(["aggressive", "neutral", "conservative"] as const).map((role) => ({
      ...base,
      id: `risk-${role}`,
      stage: "risk",
      role,
      round: 0,
      content: {
        role,
        executive_summary: `${role} risk summary.`,
        findings: [
          {
            id: `risk.${role}.finding_1`,
            kind: role === "aggressive" ? "upside_omission" : "downside",
            statement: `${role} finding.`,
            mechanism: "Evidence uncertainty widens outcomes.",
            severity: "medium",
            related_claim_ids: ["market.claim_1"],
            evidence_refs: [firstRef],
          },
        ],
        invalidation_paths: ["The mechanism fails."],
        recommended_changes: ["Reduce confidence."],
        confidence_adjustment: -0.05,
        evidence_refs: [firstRef],
      },
    })),
    {
      ...base,
      id: "decision",
      stage: "decision",
      role: "final_committee",
      round: 0,
      content: decision,
    },
  ] as unknown as ResearchArtifact[];
}
