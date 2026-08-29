import { describe, expect, test } from "vitest";

import type { RunEvent } from "./api/client";
import { aggregateRunActivity } from "./runActivity";

function event(
  sequence: number,
  attempt: number,
  eventType: string,
  node: string | null,
): RunEvent {
  return {
    run_id: "run",
    sequence,
    attempt,
    event_type: eventType,
    node,
    payload: {},
    created_at: `2026-08-29T00:00:${String(sequence).padStart(2, "0")}Z`,
  };
}

describe("aggregateRunActivity", () => {
  test("merges repeated node events and preserves parallel Full analyst units", () => {
    const attempts = aggregateRunActivity([
      event(1, 1, "node.started", "analyst.news.report"),
      event(2, 1, "node.started", "analyst.market.report"),
      event(3, 1, "artifact.created", "analyst.news.report"),
      event(4, 1, "node.completed", "analyst.news.report"),
    ], "full");

    expect(attempts).toHaveLength(1);
    expect(attempts[0].workUnits).toHaveLength(2);
    expect(attempts[0].workUnits[0]).toMatchObject({
      node: "analyst.news.report",
      stage: "analyst_reports",
      role: "news",
      state: "completed",
    });
    expect(attempts[0].workUnits[0].events).toHaveLength(3);
    expect(attempts[0].workUnits[1].state).toBe("running");
  });

  test("orders Incremental attempts newest-first and exposes retry, recovery, and unknown nodes", () => {
    const attempts = aggregateRunActivity([
      event(1, 1, "node.output_retry", "incremental.synthesis.serialize"),
      event(2, 1, "run.failed", null),
      event(3, 2, "node.output_recovered", "incremental.synthesis.serialize"),
      event(4, 2, "node.started", "vendor.unmapped.step"),
    ], "incremental");

    expect(attempts.map((attempt) => attempt.attempt)).toEqual([2, 1]);
    expect(attempts[0].workUnits[0]).toMatchObject({
      stage: "incremental_serialization",
      state: "recovered",
    });
    expect(attempts[0].workUnits[1]).toMatchObject({
      node: "vendor.unmapped.step",
      stage: "workflow",
      state: "running",
    });
    expect(attempts[1].state).toBe("failed");
  });

  test("uses event order for recovery and keeps retry and degradation signals visible", () => {
    const attempts = aggregateRunActivity([
      event(1, 1, "node.output_retry", "analyst.news.audit"),
      event(2, 1, "node.output_recovered", "analyst.news.audit"),
      event(3, 1, "node.numeric_audit_degraded", "committee.final.serialize.numeric"),
    ], "full");

    expect(attempts[0].workUnits[0]).toMatchObject({
      action: "audit",
      state: "recovered",
      signals: ["retrying", "recovered"],
    });
    expect(attempts[0].workUnits[1]).toMatchObject({
      action: "serialize",
      state: "degraded",
      signals: ["degraded"],
    });
  });

  test("maps node-less Incremental lifecycle events to readable stages and actions", () => {
    const attempts = aggregateRunActivity([
      event(1, 1, "incremental.collection_completed", null),
      event(2, 1, "incremental.synthesis_started", null),
    ], "incremental");

    expect(attempts[0].workUnits).toEqual(expect.arrayContaining([
      expect.objectContaining({
        node: "incremental.collection",
        stage: "collection",
        action: "collect",
      }),
      expect.objectContaining({
        node: "incremental.synthesis",
        stage: "incremental_semantic",
        action: "synthesize",
      }),
    ]));
  });

  test("treats cancellation as a completed lifecycle unit", () => {
    const attempts = aggregateRunActivity([
      event(1, 1, "run.started", null),
      event(2, 1, "run.cancelled", null),
    ], "full");

    expect(attempts[0]).toMatchObject({
      state: "completed",
      currentStage: "commit",
    });
    expect(attempts[0].workUnits[0]).toMatchObject({
      node: "run.lifecycle",
      state: "completed",
    });
  });

  test("uses the terminal lifecycle event as the successful current stage", () => {
    const attempts = aggregateRunActivity([
      event(1, 1, "run.started", null),
      event(2, 1, "node.started", "analyst.news.report"),
      event(3, 1, "node.completed", "analyst.news.report"),
      event(4, 1, "run.succeeded", null),
    ], "full");

    expect(attempts[0]).toMatchObject({
      state: "completed",
      currentStage: "commit",
    });
  });

  test("keeps a recovered attempt and shared stage running while parallel work remains", () => {
    const attempts = aggregateRunActivity([
      event(1, 1, "node.output_retry", "analyst.news.audit"),
      event(2, 1, "node.output_recovered", "analyst.news.audit"),
      event(3, 1, "node.started", "analyst.market.report"),
      event(4, 1, "node.completed", "analyst.fundamentals.report"),
    ], "full");

    expect(attempts[0]).toMatchObject({
      state: "running",
      currentStage: "analyst_reports",
      stageStates: { analyst_reports: "running" },
    });
  });
});
