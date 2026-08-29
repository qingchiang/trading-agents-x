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
});
