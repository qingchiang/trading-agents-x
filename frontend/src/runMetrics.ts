import type {
  ArtifactGenerationObservation,
  ResearchArtifact,
  RunEvent,
  RunMetrics,
} from "./api/client";

export type MetricPhase =
  | "collect"
  | "context"
  | "reasonWrite"
  | "audit"
  | "semanticStructured"
  | "schemaSerialization"
  | "workflowOther";

export type OutputStatus =
  | "normal"
  | "retry"
  | "recovered"
  | "auditIncomplete"
  | "degraded"
  | "failed";

type Usage = {
  llmCalls: number;
  toolCalls: number;
  inputTokens: number;
  cacheHitInputTokens: number;
  cacheMissInputTokens: number;
  outputTokens: number;
  reasoningOutputTokens: number;
  detailedUsageCalls: number;
  activeTime: number;
};

export type NodeMetricRow = Usage & {
  node: string;
  phase: MetricPhase;
  outputStatus: OutputStatus;
  observations: ArtifactGenerationObservation[];
};

export type RoleMetricGroup = Usage & {
  id: string;
  labelKey: string;
  outputStatus: OutputStatus;
  nodes: NodeMetricRow[];
  firstSequence: number;
};

export type ContextMetricRow = {
  sequence: number;
  node: string;
  inlineCharacters: number;
  referenceCount: number;
  tableSummaryCount: number;
  catalogItems: number;
};

const statusPriority: Record<OutputStatus, number> = {
  normal: 0,
  retry: 1,
  recovered: 2,
  auditIncomplete: 3,
  degraded: 4,
  failed: 5,
};

export function buildRoleMetricGroups(
  metrics: RunMetrics | undefined,
  events: RunEvent[],
  artifacts: ResearchArtifact[],
): RoleMetricGroup[] {
  const observations = new Map<
    string,
    ArtifactGenerationObservation[]
  >();
  for (const artifact of artifacts) {
    for (const observation of artifact.generation_observations ?? []) {
      observations.set(observation.node, [
        ...(observations.get(observation.node) ?? []),
        observation,
      ]);
    }
  }

  const firstSequence = new Map<string, number>();
  for (const event of events) {
    if (event.node && !firstSequence.has(event.node)) {
      firstSequence.set(event.node, event.sequence);
    }
  }

  const groups = new Map<string, RoleMetricGroup>();
  for (const [node, rawUsage] of Object.entries(metrics?.node_metrics ?? {})) {
    const role = metricRole(node);
    const row: NodeMetricRow = {
      node,
      phase: metricPhase(node),
      outputStatus: nodeOutputStatus(node, events),
      observations: observations.get(node) ?? [],
      ...usage(rawUsage),
    };
    const existing = groups.get(role.id);
    if (existing) {
      existing.nodes.push(row);
      addUsage(existing, row);
      existing.outputStatus = higherStatus(
        existing.outputStatus,
        row.outputStatus,
      );
      existing.firstSequence = Math.min(
        existing.firstSequence,
        firstSequence.get(node) ?? firstRoleSequence(role.id, events),
      );
      continue;
    }
    groups.set(role.id, {
      id: role.id,
      labelKey: role.labelKey,
      outputStatus: row.outputStatus,
      nodes: [row],
      firstSequence:
        firstSequence.get(node) ?? firstRoleSequence(role.id, events),
      ...usage(rawUsage),
    });
  }

  for (const group of groups.values()) {
    group.nodes.sort(
      (left, right) =>
        (firstSequence.get(left.node) ?? Number.MAX_SAFE_INTEGER) -
          (firstSequence.get(right.node) ?? Number.MAX_SAFE_INTEGER) ||
        left.node.localeCompare(right.node),
    );
  }
  return [...groups.values()].sort(
    (left, right) =>
      left.firstSequence - right.firstSequence || left.id.localeCompare(right.id),
  );
}

export function metricPhase(node: string): MetricPhase {
  if (node.endsWith(".context")) return "context";
  if (
    node === "debate.agenda.serialize" ||
    node.endsWith(".serialize.numeric")
  ) {
    return "semanticStructured";
  }
  if (node.endsWith(".serialize.core") || node.endsWith(".serialize")) {
    return "schemaSerialization";
  }
  if (node.endsWith(".audit")) return "audit";
  if (
    node.endsWith(".report") ||
    node.endsWith(".write") ||
    node.endsWith(".reason")
  ) {
    return "reasonWrite";
  }
  if (node.endsWith(".collect") || /^analyst\.[^.]+$/.test(node)) {
    return "collect";
  }
  return "workflowOther";
}

export function nodeOutputStatus(
  node: string,
  events: RunEvent[],
): OutputStatus {
  let status: OutputStatus = "normal";
  for (const event of events) {
    if (event.node !== node) continue;
    let candidate: OutputStatus | null = null;
    if (event.event_type === "node.numeric_audit_degraded") {
      candidate = "degraded";
    } else if (event.event_type === "node.output_failed") {
      candidate = node.endsWith(".audit") ? "auditIncomplete" : "failed";
    } else if (
      event.event_type === "node.output_recovered" ||
      event.event_type === "node.numeric_audit_recovered"
    ) {
      candidate = "recovered";
    } else if (
      event.event_type === "node.output_retry" ||
      event.event_type === "node.numeric_audit_retry"
    ) {
      candidate = "retry";
    }
    if (candidate) status = higherStatus(status, candidate);
  }
  return status;
}

export function contextMetricRows(events: RunEvent[]): ContextMetricRow[] {
  return events
    .filter(
      (event) =>
        event.event_type === "node.context_prepared" &&
        typeof event.node === "string",
    )
    .map((event) => ({
      sequence: event.sequence,
      node: event.node ?? "context",
      inlineCharacters: numericPayload(event.payload, "inline_characters"),
      referenceCount: numericPayload(event.payload, "reference_count"),
      tableSummaryCount: numericPayload(event.payload, "table_summary_count"),
      catalogItems: numericPayload(event.payload, "catalog_items"),
    }))
    .sort((left, right) => left.sequence - right.sequence);
}

function metricRole(node: string): { id: string; labelKey: string } {
  const analyst = /^analyst\.([^.]+)/.exec(node)?.[1];
  if (analyst) {
    const analystLabels: Record<string, string> = {
      market: "marketAnalyst",
      social: "socialAnalyst",
      news: "newsAnalyst",
      fundamentals: "fundamentalsAnalyst",
    };
    return {
      id: `analyst.${analyst}`,
      labelKey: analystLabels[analyst] ?? "unknownAnalyst",
    };
  }
  if (node.startsWith("case.bull")) return { id: "case.bull", labelKey: "bullCase" };
  if (node.startsWith("case.bear")) return { id: "case.bear", labelKey: "bearCase" };
  if (node.startsWith("debate.agenda")) {
    return { id: "debate.moderator", labelKey: "debateModerator" };
  }
  if (node.startsWith("rebuttal.bull")) {
    return { id: "rebuttal.bull", labelKey: "bullRebuttal" };
  }
  if (node.startsWith("rebuttal.bear")) {
    return { id: "rebuttal.bear", labelKey: "bearRebuttal" };
  }
  if (node.startsWith("judge.research")) {
    return { id: "judge.research", labelKey: "researchJudge" };
  }
  if (node.startsWith("risk.review")) {
    return { id: "risk.review", labelKey: "riskReview" };
  }
  for (const lens of ["aggressive", "neutral", "conservative"] as const) {
    if (node.startsWith(`risk.${lens}`)) {
      return { id: `risk.${lens}`, labelKey: `riskLens${capitalize(lens)}` };
    }
  }
  if (node.startsWith("committee.final")) {
    return { id: "committee.final", labelKey: "finalCommittee" };
  }
  return { id: "workflow", labelKey: "workflowSystem" };
}

function firstRoleSequence(roleId: string, events: RunEvent[]): number {
  return events.reduce((first, event) => {
    if (!event.node || metricRole(event.node).id !== roleId) return first;
    return Math.min(first, event.sequence);
  }, Number.MAX_SAFE_INTEGER);
}

function usage(raw: Record<string, number | undefined>): Usage {
  return {
    llmCalls: raw.llm_calls ?? 0,
    toolCalls: raw.tool_calls ?? 0,
    inputTokens: raw.input_tokens ?? 0,
    cacheHitInputTokens: raw.cache_hit_input_tokens ?? 0,
    cacheMissInputTokens: raw.cache_miss_input_tokens ?? 0,
    outputTokens: raw.output_tokens ?? 0,
    reasoningOutputTokens: raw.reasoning_output_tokens ?? 0,
    detailedUsageCalls: raw.detailed_usage_calls ?? 0,
    activeTime: raw.wall_time_seconds ?? 0,
  };
}

function addUsage(target: Usage, source: Usage) {
  target.llmCalls += source.llmCalls;
  target.toolCalls += source.toolCalls;
  target.inputTokens += source.inputTokens;
  target.cacheHitInputTokens += source.cacheHitInputTokens;
  target.cacheMissInputTokens += source.cacheMissInputTokens;
  target.outputTokens += source.outputTokens;
  target.reasoningOutputTokens += source.reasoningOutputTokens;
  target.detailedUsageCalls += source.detailedUsageCalls;
  target.activeTime += source.activeTime;
}

function higherStatus(left: OutputStatus, right: OutputStatus): OutputStatus {
  return statusPriority[right] > statusPriority[left] ? right : left;
}

function capitalize(value: string): string {
  return `${value[0].toUpperCase()}${value.slice(1)}`;
}

function numericPayload(
  payload: Record<string, unknown> | undefined,
  key: string,
): number {
  const value = payload?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}
