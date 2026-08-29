import type { RunEvent } from "./api/client";

export type ActivityStage =
  | "collection"
  | "analyst_reports"
  | "research_cases"
  | "debate"
  | "research_judgment"
  | "risk_review"
  | "final_decision"
  | "incremental_semantic"
  | "incremental_serialization"
  | "commit"
  | "workflow";

export type ActivityState =
  | "pending"
  | "running"
  | "completed"
  | "retrying"
  | "recovered"
  | "failed";

export interface ActivityWorkUnit {
  key: string;
  node: string;
  stage: ActivityStage;
  role: string | null;
  state: ActivityState;
  events: RunEvent[];
  firstSequence: number;
  lastSequence: number;
}

export interface ActivityAttempt {
  attempt: number;
  state: ActivityState;
  workUnits: ActivityWorkUnit[];
  currentStage: ActivityStage;
}

const statePriority: Record<ActivityState, number> = {
  pending: 0,
  running: 1,
  completed: 2,
  recovered: 3,
  retrying: 4,
  failed: 5,
};

export function aggregateRunActivity(
  events: RunEvent[],
  researchKind: "full" | "incremental",
): ActivityAttempt[] {
  const attempts = new Map<number, RunEvent[]>();
  for (const event of [...events].sort((left, right) => left.sequence - right.sequence)) {
    const bucket = attempts.get(event.attempt) ?? [];
    bucket.push(event);
    attempts.set(event.attempt, bucket);
  }
  return [...attempts.entries()]
    .sort(([left], [right]) => right - left)
    .map(([attempt, attemptEvents]) => {
      const units = new Map<string, RunEvent[]>();
      for (const event of attemptEvents) {
        const node = activityNode(event);
        const bucket = units.get(node) ?? [];
        bucket.push(event);
        units.set(node, bucket);
      }
      const workUnits = [...units.entries()].map(([node, unitEvents]) => ({
        key: `${attempt}:${node}`,
        node,
        stage: activityStage(node, researchKind),
        role: activityRole(node),
        state: unitState(unitEvents),
        events: unitEvents,
        firstSequence: unitEvents[0].sequence,
        lastSequence: unitEvents.at(-1)?.sequence ?? unitEvents[0].sequence,
      }));
      const lastActive = [...workUnits]
        .reverse()
        .find((unit) => unit.state !== "completed") ?? workUnits.at(-1);
      return {
        attempt,
        state: attemptState(attemptEvents, workUnits),
        workUnits,
        currentStage: lastActive?.stage ?? "workflow",
      };
    });
}

function activityNode(event: RunEvent): string {
  if (event.node) return event.node;
  if (event.event_type.startsWith("incremental.collection")) return "incremental.collection";
  if (event.event_type.startsWith("incremental.synthesis")) return "incremental.synthesis";
  if (event.event_type === "evidence.sealed") return "evidence.seal";
  if (event.event_type.startsWith("run.")) return "run.lifecycle";
  return `event.${event.event_type}`;
}

function unitState(events: RunEvent[]): ActivityState {
  let state: ActivityState = "pending";
  for (const event of events) {
    let candidate: ActivityState | null = null;
    if (event.event_type.includes("failed") || event.event_type === "run.failed") candidate = "failed";
    else if (event.event_type.includes("retry")) candidate = "retrying";
    else if (event.event_type.includes("recovered")) candidate = "recovered";
    else if (
      event.event_type.endsWith("completed") ||
      event.event_type === "artifact.created" ||
      event.event_type === "evidence.sealed" ||
      event.event_type === "run.succeeded"
    ) candidate = "completed";
    else if (event.event_type.endsWith("started") || event.event_type === "run.resumed") candidate = "running";
    if (candidate && statePriority[candidate] >= statePriority[state]) state = candidate;
  }
  return state;
}

function attemptState(events: RunEvent[], units: ActivityWorkUnit[]): ActivityState {
  const terminal = [...events].reverse().find((event) => event.event_type.startsWith("run."));
  if (terminal?.event_type === "run.failed") return "failed";
  if (terminal?.event_type === "run.succeeded" || terminal?.event_type === "run.cancelled") return "completed";
  if (units.some((unit) => unit.state === "retrying")) return "retrying";
  return "running";
}

function activityStage(node: string, kind: "full" | "incremental"): ActivityStage {
  const normalized = node.toLowerCase();
  if (kind === "incremental") {
    if (normalized.includes("collection") || normalized.includes("evidence")) return "collection";
    if (normalized.includes("semantic")) return "incremental_semantic";
    if (
      normalized.includes("serialize") ||
      normalized.includes("reassessment") ||
      normalized.includes("synthesis.decision")
    ) return "incremental_serialization";
    if (normalized.includes("commit") || normalized === "run.lifecycle") return "commit";
  }
  if (normalized.includes("analyst.")) {
    return normalized.endsWith(".collect") ? "collection" : "analyst_reports";
  }
  if (normalized.includes("bull") || normalized.includes("bear")) return "research_cases";
  if (normalized.includes("debate") || normalized.includes("rebuttal")) return "debate";
  if (normalized.includes("judge") || normalized.includes("research.")) return "research_judgment";
  if (normalized.includes("risk")) return "risk_review";
  if (normalized.includes("committee") || normalized.includes("final")) return "final_decision";
  if (normalized.includes("evidence") || normalized.includes("collect")) return "collection";
  if (normalized === "run.lifecycle") return "commit";
  return "workflow";
}

function activityRole(node: string): string | null {
  for (const role of ["market", "social", "news", "fundamentals", "bull", "bear", "judge", "risk"]) {
    if (node.toLowerCase().split(/[._-]/).includes(role)) return role;
  }
  return null;
}
