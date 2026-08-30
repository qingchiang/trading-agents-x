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

  test("keeps cancellation authoritative while completing its lifecycle unit", () => {
    const attempts = aggregateRunActivity([
      event(1, 1, "run.started", null),
      event(2, 1, "run.cancelled", null),
    ], "full");

    expect(attempts[0]).toMatchObject({
      state: "cancelled",
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

  test("completes an unfinished unit when the attempt succeeds", () => {
    const attempts = aggregateRunActivity([
      event(1, 1, "node.started", "analyst.news.report"),
      event(2, 1, "run.succeeded", null),
    ], "full", { currentAttempt: 1, runStatus: "succeeded" });

    expect(attempts[0].state).toBe("completed");
    expect(attempts[0].workUnits[0]).toMatchObject({
      node: "analyst.news.report",
      state: "completed",
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

  test("orders work by research stage and interrupts unfinished units after failure", () => {
    const attempts = aggregateRunActivity([
      event(1, 1, "run.started", null),
      event(2, 1, "incremental.synthesis_started", null),
      event(3, 1, "phase.started", "incremental.synthesis.serialize"),
      event(4, 1, "node.output_failed", "incremental.synthesis.serialize"),
      event(5, 1, "phase.completed", "incremental.synthesis.serialize"),
      event(6, 1, "run.failed", null),
    ], "incremental", { currentAttempt: 1, runStatus: "failed" });

    expect(attempts[0]).toMatchObject({
      state: "failed",
      currentStage: "commit",
      stageStates: {
        incremental_semantic: "interrupted",
        incremental_serialization: "failed",
        commit: "failed",
      },
    });
    expect(attempts[0].workUnits.map((unit) => unit.node)).toEqual([
      "incremental.synthesis",
      "incremental.synthesis.serialize",
      "run.lifecycle",
    ]);
    expect(attempts[0].workUnits[0].state).toBe("interrupted");
    expect(attempts[0].workUnits[1].state).toBe("failed");
  });

  test("reconciles an anonymized four-attempt history to the final success", () => {
    const attempts = aggregateRunActivity(retriedIncrementalHistory(), "incremental", {
      currentAttempt: 4,
      runStatus: "succeeded",
    });

    expect(attempts.map((attempt) => attempt.attempt)).toEqual([4, 3, 2, 1]);
    expect(attempts[0]).toMatchObject({
      state: "completed",
      currentStage: "commit",
    });
    expect(attempts.slice(1).map((attempt) => attempt.state)).toEqual([
      "failed",
      "failed",
      "failed",
    ]);
    expect(attempts[0].workUnits.map((unit) => unit.node)).toEqual([
      "incremental.collection",
      "incremental.synthesis",
      "incremental.synthesis.semantic",
      "incremental.synthesis.serialize",
      "evidence.seal",
      "run.lifecycle",
    ]);
    expect(
      attempts[0].workUnits.find(
        (unit) => unit.node === "incremental.synthesis.serialize",
      ),
    ).toMatchObject({
      state: "completed",
      signals: ["retrying", "recovered"],
    });
  });

  test("uses the persisted current attempt as the authoritative live state", () => {
    const attempts = aggregateRunActivity([
      event(1, 1, "run.failed", null),
    ], "incremental", { currentAttempt: 2, runStatus: "queued" });

    expect(attempts[0]).toMatchObject({
      attempt: 2,
      state: "pending",
      currentStage: "workflow",
      workUnits: [],
    });
    expect(attempts[1].state).toBe("failed");
  });

  test("keeps the persisted current attempt first while a future attempt refresh is pending", () => {
    const attempts = aggregateRunActivity([
      event(1, 1, "run.failed", null),
      event(2, 2, "run.retry_queued", null),
    ], "incremental", { currentAttempt: 1, runStatus: "failed" });

    expect(attempts.map((attempt) => attempt.attempt)).toEqual([1, 2]);
    expect(attempts[0].state).toBe("failed");
  });

  test("keeps the terminal lifecycle unit after unknown workflow units", () => {
    const attempts = aggregateRunActivity([
      event(1, 1, "node.started", "vendor.unmapped.step"),
      event(2, 1, "run.failed", null),
    ], "incremental", { currentAttempt: 1, runStatus: "failed" });

    expect(attempts[0].workUnits.map((unit) => unit.node)).toEqual([
      "vendor.unmapped.step",
      "run.lifecycle",
    ]);
  });
});

function retriedIncrementalHistory(): RunEvent[] {
  const rows: Array<[number, number, string, string | null]> = [
    [1, 1, "run.queued", null],
    [2, 1, "run.started", null],
    [3, 1, "incremental.collection_completed", null],
    [4, 1, "incremental.synthesis_started", null],
    [5, 1, "phase.started", "incremental.synthesis.semantic"],
    [6, 1, "phase.completed", "incremental.synthesis.semantic"],
    [7, 1, "phase.started", "incremental.synthesis.serialize"],
    [8, 1, "node.output_retry", "incremental.synthesis.serialize"],
    [9, 1, "node.output_failed", "incremental.synthesis.serialize"],
    [10, 1, "phase.completed", "incremental.synthesis.serialize"],
    [11, 1, "run.failed", null],
    [12, 2, "run.retry_queued", null],
    [13, 2, "run.started", null],
    [14, 2, "incremental.collection_completed", null],
    [15, 2, "incremental.synthesis_started", null],
    [16, 2, "phase.started", "incremental.synthesis.semantic"],
    [17, 2, "phase.completed", "incremental.synthesis.semantic"],
    [18, 2, "phase.started", "incremental.synthesis.serialize"],
    [19, 2, "node.output_retry", "incremental.synthesis.serialize"],
    [20, 2, "node.output_failed", "incremental.synthesis.serialize"],
    [21, 2, "phase.completed", "incremental.synthesis.serialize"],
    [22, 2, "run.failed", null],
    [23, 3, "run.retry_queued", null],
    [24, 3, "run.started", null],
    [25, 3, "incremental.collection_completed", null],
    [26, 3, "incremental.synthesis_started", null],
    [27, 3, "phase.started", "incremental.synthesis.semantic"],
    [28, 3, "phase.completed", "incremental.synthesis.semantic"],
    [29, 3, "phase.started", "incremental.synthesis.serialize"],
    [30, 3, "node.output_retry", "incremental.synthesis.serialize"],
    [31, 3, "node.output_failed", "incremental.synthesis.decision"],
    [32, 3, "node.output_failed", "incremental.synthesis.serialize"],
    [33, 3, "phase.completed", "incremental.synthesis.serialize"],
    [34, 3, "run.failed", null],
    [35, 4, "run.retry_queued", null],
    [36, 4, "run.started", null],
    [37, 4, "incremental.collection_completed", null],
    [38, 4, "incremental.synthesis_started", null],
    [39, 4, "phase.started", "incremental.synthesis.semantic"],
    [40, 4, "phase.completed", "incremental.synthesis.semantic"],
    [41, 4, "phase.started", "incremental.synthesis.serialize"],
    [42, 4, "node.output_retry", "incremental.synthesis.serialize"],
    [43, 4, "node.output_recovered", "incremental.synthesis.serialize"],
    [44, 4, "phase.completed", "incremental.synthesis.serialize"],
    [45, 4, "incremental.synthesis_completed", null],
    [46, 4, "evidence.sealed", "evidence.seal"],
    [47, 4, "run.succeeded", null],
  ];
  return rows.map(([sequence, attempt, eventType, node]) =>
    event(sequence, attempt, eventType, node),
  );
}
