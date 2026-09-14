import { describe, expect, test } from "vitest";

import type { ResearchDecision } from "./api/client";
import {
  baselineComponentText,
  groupReassessment,
  reassessmentDispositionCounts,
} from "./reassessment";

const decision = {
  executive_summary: "Summary",
  thesis: "Thesis",
  catalysts: ["Catalyst A"],
  risks: ["Risk A"],
  invalidation_conditions: ["Invalidation A"],
  scenarios: [
    {
      kind: "base",
      outcome: "Base outcome",
      core_assumptions: ["Base assumption"],
    },
    { kind: "bull", outcome: "Bull outcome", core_assumptions: [] },
    { kind: "bear", outcome: "Bear outcome", core_assumptions: [] },
  ],
  risk_review_adjustments: [{ explanation: "Risk adjustment" }],
} as ResearchDecision;

describe("reassessment mapping", () => {
  test("groups components without assuming current and baseline array indexes align", () => {
    const entries = [
      {
        component_id: "thesis",
        disposition: "weakened",
        reason: "Changed",
        evidence_refs: [],
      },
      {
        component_id: "scenarios.base.core_assumptions.0",
        disposition: "reaffirmed",
        reason: "Held",
        evidence_refs: [],
      },
      {
        component_id: "risks.0",
        disposition: "overturned",
        reason: "Removed",
        evidence_refs: [],
      },
    ] as Parameters<typeof groupReassessment>[0];
    const groups = groupReassessment(entries);
    expect(groups.map((group) => group.key)).toEqual(["core", "risks", "base"]);
    expect(groups.find(group => group.key === "risks")?.entries[0].reason).toBe("Removed");
    expect(
      baselineComponentText(
        decision,
        "scenarios.base.core_assumptions.0",
      ),
    ).toBe("Base assumption");
    expect(reassessmentDispositionCounts(entries)).toEqual({
      weakened: 1,
      reaffirmed: 1,
      overturned: 1,
    });
  });
});
