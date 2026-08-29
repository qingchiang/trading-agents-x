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
  | "degraded"
  | "failed";

export type ActivityAction =
  | "collect"
  | "synthesize"
  | "audit"
  | "serialize"
  | "prepare"
  | "debate"
  | "review"
  | "commit"
  | "process";

export type ActivitySignal = "retrying" | "recovered" | "degraded" | "failed";

export interface ActivityWorkUnit {
  key: string;
  node: string;
  stage: ActivityStage;
  role: string | null;
  action: ActivityAction;
  state: ActivityState;
  signals: ActivitySignal[];
  events: RunEvent[];
  firstSequence: number;
  lastSequence: number;
}

export interface ActivityAttempt {
  attempt: number;
  state: ActivityState;
  workUnits: ActivityWorkUnit[];
  currentStage: ActivityStage;
  stageStates: Partial<Record<ActivityStage, ActivityState>>;
}

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
        action: activityAction(node),
        state: unitState(unitEvents),
        signals: unitSignals(unitEvents),
        events: unitEvents,
        firstSequence: unitEvents[0].sequence,
        lastSequence: unitEvents.at(-1)?.sequence ?? unitEvents[0].sequence,
      }));
      const currentUnit = currentWorkUnit(attemptEvents, workUnits);
      return {
        attempt,
        state: attemptState(attemptEvents, workUnits),
        workUnits,
        currentStage: currentUnit?.stage ?? "workflow",
        stageStates: aggregateStageStates(workUnits),
      };
    });
}

function currentWorkUnit(
  events: RunEvent[],
  units: ActivityWorkUnit[],
): ActivityWorkUnit | undefined {
  const terminal = [...events].reverse().find((event) =>
    ["run.succeeded", "run.failed", "run.cancelled"].includes(event.event_type),
  );
  if (terminal) {
    return units.find((unit) => unit.events.includes(terminal));
  }
  const newestFirst = [...units].sort(
    (left, right) => right.lastSequence - left.lastSequence,
  );
  return newestFirst.find((unit) =>
    ["pending", "running", "retrying", "failed"].includes(unit.state),
  ) ?? newestFirst[0];
}

function aggregateStageStates(
  units: ActivityWorkUnit[],
): Partial<Record<ActivityStage, ActivityState>> {
  const grouped = new Map<ActivityStage, ActivityState[]>();
  for (const unit of units) {
    const states = grouped.get(unit.stage) ?? [];
    states.push(unit.state);
    grouped.set(unit.stage, states);
  }
  return Object.fromEntries(
    [...grouped].map(([stage, states]) => [stage, aggregateState(states)]),
  );
}

function aggregateState(states: ActivityState[]): ActivityState {
  for (const state of [
    "failed",
    "retrying",
    "running",
    "degraded",
    "recovered",
    "completed",
    "pending",
  ] as const) {
    if (states.includes(state)) return state;
  }
  return "pending";
}

function activityNode(event: RunEvent): string {
  if (event.node) return event.node;
  if (event.event_type.startsWith("incremental.collection")) return "incremental.collection";
  if (event.event_type === "incremental.no_advancement") return "incremental.collection";
  if (event.event_type.startsWith("incremental.synthesis")) return "incremental.synthesis";
  if (event.event_type === "evidence.sealed") return "evidence.seal";
  if (event.event_type.startsWith("run.")) return "run.lifecycle";
  return `event.${event.event_type}`;
}

function unitState(events: RunEvent[]): ActivityState {
  let state: ActivityState = "pending";
  for (const event of events) {
    const candidate = eventState(event);
    if (candidate) state = candidate;
  }
  return state;
}

function unitSignals(events: RunEvent[]): ActivitySignal[] {
  const signals: ActivitySignal[] = [];
  for (const event of events) {
    const candidate = eventState(event);
    if (
      candidate &&
      ["retrying", "recovered", "degraded", "failed"].includes(candidate) &&
      !signals.includes(candidate as ActivitySignal)
    ) {
      signals.push(candidate as ActivitySignal);
    }
  }
  return signals;
}

function eventState(event: RunEvent): ActivityState | null {
  if (event.event_type.includes("degraded")) return "degraded";
  if (event.event_type.includes("failed") || event.event_type === "run.failed") return "failed";
  if (event.event_type.includes("retry")) return "retrying";
  if (event.event_type.includes("recovered")) return "recovered";
  if (
    event.event_type.endsWith("completed") ||
    event.event_type === "artifact.created" ||
    event.event_type === "evidence.sealed" ||
    event.event_type === "run.succeeded" ||
    event.event_type === "run.cancelled"
  ) return "completed";
  if (event.event_type.endsWith("started") || event.event_type === "run.resumed") return "running";
  return null;
}

function attemptState(events: RunEvent[], units: ActivityWorkUnit[]): ActivityState {
  const terminal = [...events].reverse().find((event) => event.event_type.startsWith("run."));
  if (terminal?.event_type === "run.failed") return "failed";
  if (terminal?.event_type === "run.succeeded" || terminal?.event_type === "run.cancelled") return "completed";
  if (units.some((unit) => unit.state === "failed")) return "failed";
  if (units.some((unit) => unit.state === "retrying")) return "retrying";
  if (units.some((unit) => unit.state === "running")) return "running";
  if (units.some((unit) => unit.state === "degraded")) return "degraded";
  if (units.some((unit) => unit.state === "recovered")) return "recovered";
  if (units.length > 0 && units.every((unit) => unit.state === "completed")) {
    return "completed";
  }
  return "running";
}

function activityStage(node: string, kind: "full" | "incremental"): ActivityStage {
  const normalized = node.toLowerCase();
  if (kind === "incremental") {
    if (normalized.includes("collection") || normalized.includes("evidence")) return "collection";
    if (normalized.includes("semantic") || normalized === "incremental.synthesis") {
      return "incremental_semantic";
    }
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

function activityAction(node: string): ActivityAction {
  const normalized = node.toLowerCase();
  if (normalized.includes("audit")) return "audit";
  if (normalized.includes("serialize")) return "serialize";
  if (normalized.includes("collect") || normalized.includes("evidence")) return "collect";
  if (normalized.includes("context")) return "prepare";
  if (normalized.includes("debate") || normalized.includes("rebuttal")) return "debate";
  if (normalized.includes("risk") || normalized.includes("judge")) return "review";
  if (
    normalized.includes("semantic") ||
    normalized.includes("synthesis") ||
    normalized.includes("report") ||
    normalized.includes("write") ||
    normalized.includes("reason")
  ) return "synthesize";
  if (normalized.includes("commit") || normalized === "run.lifecycle") return "commit";
  return "process";
}
