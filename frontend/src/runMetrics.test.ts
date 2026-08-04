import { expect, test } from "vitest";

import type {
  ResearchArtifact,
  RunEvent,
  RunMetrics,
} from "./api/client";
import {
  buildRoleMetricGroups,
  metricPhase,
  nodeOutputStatus,
} from "./runMetrics";

function event(
  sequence: number,
  node: string,
  eventType: string,
): RunEvent {
  return {
    run_id: "run-1",
    attempt: 1,
    sequence,
    event_type: eventType,
    node,
    payload: {},
    created_at: `2026-08-04T00:00:0${sequence}Z`,
  };
}

const usage = (calls: number, tokens: number, seconds: number) => ({
  llm_calls: calls,
  tool_calls: 0,
  input_tokens: tokens,
  output_tokens: tokens,
  cache_hit_input_tokens: tokens,
  cache_miss_input_tokens: 0,
  reasoning_output_tokens: tokens,
  detailed_usage_calls: calls,
  wall_time_seconds: seconds,
});

test("groups phase metrics by role in persisted event order", () => {
  const metrics = {
    node_metrics: {
      "analyst.market.collect": usage(1, 10, 1),
      "committee.final.serialize.numeric": usage(1, 20, 2),
      "committee.final.serialize.core": usage(1, 30, 3),
      "custom.cleanup": usage(0, 0, 0.5),
    },
  } as RunMetrics;
  const events = [
    event(1, "committee.final.serialize.numeric", "phase.started"),
    event(2, "analyst.market.collect", "phase.started"),
    event(3, "committee.final.serialize.numeric", "node.numeric_audit_retry"),
    event(4, "committee.final.serialize.numeric", "node.numeric_audit_recovered"),
    event(5, "committee.final.serialize.numeric", "node.numeric_audit_degraded"),
    event(6, "committee.final.serialize.core", "node.output_failed"),
    event(7, "custom.cleanup", "node.started"),
  ];
  const artifacts = [
    {
      generation_observations: [
        {
          node: "committee.final.serialize.numeric",
          task_kind: "semantic_structured",
          client_role: "deep_reasoning",
          generation_method: "json_mode",
        },
      ],
    },
  ] as ResearchArtifact[];

  const groups = buildRoleMetricGroups(metrics, events, artifacts);

  expect(groups.map((group) => group.id)).toEqual([
    "committee.final",
    "analyst.market",
    "workflow",
  ]);
  expect(groups[0]).toMatchObject({
    llmCalls: 2,
    inputTokens: 50,
    outputTokens: 50,
    reasoningOutputTokens: 50,
    activeTime: 5,
    outputStatus: "failed",
  });
  expect(groups[0].nodes.map((row) => row.phase)).toEqual([
    "semanticStructured",
    "schemaSerialization",
  ]);
  expect(groups[0].nodes[0].observations[0].client_role).toBe(
    "deep_reasoning",
  );
  expect(groups[0].nodes[1].observations).toEqual([]);
  expect(groups[2].labelKey).toBe("workflowSystem");
});

test("classifies every displayed phase explicitly", () => {
  expect(metricPhase("analyst.market.collect")).toBe("collect");
  expect(metricPhase("case.bull.context")).toBe("context");
  expect(metricPhase("committee.final.reason")).toBe("reasonWrite");
  expect(metricPhase("case.bull.audit")).toBe("audit");
  expect(metricPhase("debate.agenda.serialize")).toBe("semanticStructured");
  expect(metricPhase("committee.final.serialize.numeric")).toBe(
    "semanticStructured",
  );
  expect(metricPhase("committee.final.serialize.core")).toBe(
    "schemaSerialization",
  );
  expect(metricPhase("workflow.finish")).toBe("workflowOther");
});

test("applies failed, degraded, audit, recovery, and retry priority", () => {
  const node = "committee.final.serialize.numeric";
  expect(
    nodeOutputStatus(node, [
      event(1, node, "node.numeric_audit_retry"),
      event(2, node, "node.numeric_audit_recovered"),
      event(3, node, "node.numeric_audit_degraded"),
    ]),
  ).toBe("degraded");
  expect(
    nodeOutputStatus("case.bull.audit", [
      event(1, "case.bull.audit", "node.output_failed"),
    ]),
  ).toBe("auditIncomplete");
  expect(
    nodeOutputStatus(node, [
      event(1, node, "node.numeric_audit_degraded"),
      event(2, node, "node.output_failed"),
    ]),
  ).toBe("failed");
});
