import type { ResearchDecision, ResearchNodeView } from "./api/client";

export type ReassessmentEntry =
  NonNullable<ResearchNodeView["reassessment"]>["entries"][number];

export type ReassessmentGroupKey =
  | "core"
  | "catalysts"
  | "risks"
  | "invalidation"
  | "base"
  | "bull"
  | "bear"
  | "risk_review"
  | "other";

export interface ReassessmentGroup {
  key: ReassessmentGroupKey;
  entries: ReassessmentEntry[];
  currentSnapshot: string[];
}

const groupOrder: ReassessmentGroupKey[] = [
  "core",
  "catalysts",
  "risks",
  "invalidation",
  "base",
  "bull",
  "bear",
  "risk_review",
  "other",
];

export function groupReassessment(
  entries: ReassessmentEntry[],
  current: ResearchDecision,
): ReassessmentGroup[] {
  const groups = new Map<ReassessmentGroupKey, ReassessmentEntry[]>();
  for (const entry of entries) {
    const key = reassessmentGroupKey(entry.component_id);
    groups.set(key, [...(groups.get(key) ?? []), entry]);
  }
  return groupOrder
    .filter((key) => groups.has(key))
    .map((key) => ({
      key,
      entries: groups.get(key) ?? [],
      currentSnapshot: decisionGroupSnapshot(current, key),
    }));
}

export function baselineComponentText(
  decision: ResearchDecision,
  componentId: string,
): string | null {
  if (componentId === "executive_summary") return decision.executive_summary;
  if (componentId === "thesis") return decision.thesis;
  const arrayMatch = /^(catalysts|risks|invalidation_conditions)\.(\d+)$/.exec(
    componentId,
  );
  if (arrayMatch) {
    return (
      decision[
        arrayMatch[1] as "catalysts" | "risks" | "invalidation_conditions"
      ] ?? []
    )[Number(arrayMatch[2])] ?? null;
  }
  const scenarioMatch =
    /^scenarios\.(base|bull|bear)\.(outcome|core_assumptions(?:\.(\d+))?)$/.exec(
      componentId,
    );
  if (scenarioMatch) {
    const scenario = decision.scenarios.find(
      (candidate) => candidate.kind === scenarioMatch[1],
    );
    if (!scenario) return null;
    if (scenarioMatch[2] === "outcome") return scenario.outcome;
    return scenario.core_assumptions[Number(scenarioMatch[3])] ?? null;
  }
  const riskMatch = /^risk_review_adjustments\.(\d+)\.explanation$/.exec(
    componentId,
  );
  if (riskMatch) {
    return (
      decision.risk_review_adjustments?.[Number(riskMatch[1])]?.explanation ??
      null
    );
  }
  return null;
}

export function reassessmentDispositionCounts(entries: ReassessmentEntry[]) {
  return entries.reduce<Record<string, number>>((counts, entry) => {
    counts[entry.disposition] = (counts[entry.disposition] ?? 0) + 1;
    return counts;
  }, {});
}

function reassessmentGroupKey(componentId: string): ReassessmentGroupKey {
  if (componentId === "executive_summary" || componentId === "thesis") {
    return "core";
  }
  if (componentId.startsWith("catalysts.")) return "catalysts";
  if (componentId.startsWith("risks.")) return "risks";
  if (componentId.startsWith("invalidation_conditions.")) return "invalidation";
  for (const scenario of ["base", "bull", "bear"] as const) {
    if (componentId.startsWith(`scenarios.${scenario}.`)) return scenario;
  }
  if (componentId.startsWith("risk_review_adjustments.")) return "risk_review";
  return "other";
}

function decisionGroupSnapshot(
  decision: ResearchDecision,
  key: ReassessmentGroupKey,
): string[] {
  if (key === "core") return [decision.executive_summary, decision.thesis];
  if (key === "catalysts") return decision.catalysts ?? [];
  if (key === "risks") return decision.risks;
  if (key === "invalidation") return decision.invalidation_conditions;
  if (key === "risk_review") {
    return (decision.risk_review_adjustments ?? []).map(
      (adjustment) => adjustment.explanation,
    );
  }
  if (key === "base" || key === "bull" || key === "bear") {
    const scenario = decision.scenarios.find((candidate) => candidate.kind === key);
    return scenario ? [scenario.outcome, ...scenario.core_assumptions] : [];
  }
  return [];
}
