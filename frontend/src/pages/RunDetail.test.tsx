import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import {
  api,
  type ResearchArtifact,
  type RunDetail as RunDetailType,
  type RunEvent,
} from "../api/client";
import i18n from "../i18n";
import { Router } from "../router";
import RunDetail from "./RunDetail";

vi.mock("../api/client", () => ({
  api: {
    run: vi.fn(),
    artifacts: vi.fn(),
    action: vi.fn(),
  },
}));

class FakeEventSource {
  static instance: FakeEventSource;
  listeners = new Map<string, EventListener>();
  onerror: ((event: Event) => void) | null = null;
  closed = false;

  constructor(_url: string) {
    FakeEventSource.instance = this;
  }

  addEventListener(name: string, listener: EventListener) {
    this.listeners.set(name, listener);
  }

  close() {
    this.closed = true;
  }

  emit(name: string, event: RunEvent) {
    this.listeners.get(name)?.(
      new MessageEvent(name, { data: JSON.stringify(event) }),
    );
  }
}

const detail = {
  run: {
    id: "run-1",
    parent_run_id: null,
    status: "succeeded",
    request: {
      ticker: "NVDA",
      analysis_date: "2026-07-24",
      asset_type: "stock",
      profile: "standard",
      analysts: ["market"],
      llm_provider: "openai",
      quick_model: "gpt-5.4-mini",
      deep_model: "gpt-5.5",
      quick_reasoning_effort: "provider_default",
      deep_reasoning_effort: "provider_default",
      output_language: "ja",
      provenance: true,
    },
    config_snapshot: {},
    attempt: 1,
    cancel_requested: false,
    metrics: {
      llm_calls: 0,
      tool_calls: 0,
      input_tokens: 0,
      output_tokens: 0,
      wall_time_seconds: 0,
      node_wall_times: {},
    },
    created_at: "2026-07-24T00:00:00Z",
    updated_at: "2026-07-24T00:01:00Z",
  },
  result: {
    run_id: "run-1",
    status: "succeeded",
    instrument: "NVDA",
    reports: {
      market: {
        analyst: "market",
        summary: "Summary",
        claims: [],
        confidence: 0.7,
        evidence_refs: ["ev_0123456789ab"],
        warnings: [
          {
            code: "evidence.degraded",
            message: "Historical source was partial.",
            evidence_ref: "ev_0123456789ab",
            source: "fixture",
          },
        ],
        narrative: "# Market report\n\nEvidence-grounded narrative.",
      },
    },
    decision: {
      rating: "Hold",
      confidence: 0.65,
      thesis: "Evidence is balanced.",
      evidence_refs: ["ev_0123456789ab"],
      memory_refs: ["memory:legacy-run"],
      catalysts: [],
      risks: ["Demand slows"],
      invalidation_conditions: ["New filing changes the thesis"],
      time_horizon: "6-12 months",
    },
    evidence: {
      version: "1",
      instrument: "NVDA",
      analysis_date: "2026-07-24",
      sealed_at: "2026-07-24T00:00:30Z",
      digest: "fixture-digest",
      items: [
        {
          ref: "ev_0123456789ab",
          source: "fixture",
          evidence_type: "Price snapshot",
          requested_date: "2026-07-24",
          effective_date: "2026-07-24",
          available_at: "2026-07-24T00:00:00Z",
          content: "**Close:** 100 USD",
          value: 100,
          unit: "USD",
          quality: "high",
          fallback: false,
          provenance: { vendor: "fixture-feed" },
        },
      ],
    },
    metrics: {
      llm_calls: 4,
      tool_calls: 3,
      input_tokens: 1200,
      output_tokens: 400,
      wall_time_seconds: 12.4,
      node_wall_times: {},
    },
    warnings: [],
  },
} as unknown as RunDetailType;

const artifacts = [
  {
    id: "artifact-bull",
    run_id: "run-1",
    attempt: 1,
    stage: "perspective",
    role: "bull",
    round: 0,
    schema_version: "1",
    created_at: "2026-07-24T00:00:40Z",
    content: {
      role: "bull",
      thesis: "**Demand** remains constructive.",
      claim_rebuttals: ["Valuation risk is already reflected."],
      evidence_refs: ["ev_0123456789ab"],
      new_evidence_refs: [],
      risks: ["Demand could slow."],
    },
  },
  {
    id: "artifact-judge",
    run_id: "run-1",
    attempt: 1,
    stage: "judge",
    role: "research_judge",
    round: 0,
    schema_version: "1",
    created_at: "2026-07-24T00:00:50Z",
    content: {
      rating: "Hold",
      confidence: 0.62,
      thesis: "The judge draft remains balanced.",
      evidence_refs: ["ev_0123456789ab"],
      catalysts: [],
      risks: ["Demand slows"],
      invalidation_conditions: ["New evidence supersedes the snapshot"],
      time_horizon: "6-12 months",
    },
  },
] as unknown as ResearchArtifact[];

beforeEach(async () => {
  vi.resetAllMocks();
  await i18n.changeLanguage("en");
  vi.mocked(api.run).mockResolvedValue(detail);
  vi.mocked(api.artifacts).mockResolvedValue(artifacts);
  vi.stubGlobal("EventSource", FakeEventSource);
});

test("restores deliberation and resolves evidence references across run views", async () => {
  render(
    <Router initialPath="/runs/run-1">
      <RunDetail />
    </Router>,
  );

  expect(await screen.findByRole("heading", { name: "NVDA" })).toBeVisible();
  expect(screen.getByRole("tab", { name: "Agent timeline" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.getByText("1,200")).toBeVisible();

  fireEvent.click(screen.getByRole("tab", { name: "Deliberation" }));
  expect(await screen.findByText("Demand")).toBeVisible();
  expect(screen.getByText("research_judge")).toBeVisible();

  fireEvent.click(
    screen.getAllByRole("button", {
      name: "Open evidence ev_0123456789ab",
    })[0],
  );
  expect(
    screen.getByRole("tab", { name: "Evidence" }),
  ).toHaveAttribute("aria-selected", "true");
  expect(
    await screen.findByRole("heading", { name: "Price snapshot" }),
  ).toBeVisible();
  fireEvent.click(screen.getByText("Provenance details"));
  expect(screen.getByText("fixture-feed", { exact: false })).toBeVisible();

  fireEvent.click(screen.getByRole("tab", { name: "Reports" }));
  expect(
    await screen.findByText("Historical source was partial."),
  ).toBeVisible();
  expect(await screen.findByText("ev_0123456789ab")).toBeVisible();

  fireEvent.click(screen.getByRole("tab", { name: "Decision" }));
  expect(await screen.findByText("Evidence is balanced.")).toBeVisible();
  expect(
    screen.getByRole("link", {
      name: "Open memory memory:legacy-run",
    }),
  ).toHaveAttribute(
    "href",
    "/memory?q=legacy-run#memory-legacy-run",
  );

  const artifactEvent = {
    run_id: "run-1",
    sequence: 6,
    attempt: 1,
    event_type: "artifact.created",
    node: "judge.research",
    payload: {
      artifact_id: "artifact-judge",
      stage: "judge",
      role: "research_judge",
    },
    created_at: "2026-07-24T00:00:50Z",
  } as RunEvent;
  act(() => FakeEventSource.instance.emit("artifact.created", artifactEvent));
  await vi.waitFor(() => expect(api.artifacts).toHaveBeenCalledTimes(2));

  const event = {
    run_id: "run-1",
    sequence: 7,
    attempt: 1,
    event_type: "run.succeeded",
    node: null,
    payload: {},
    created_at: "2026-07-24T00:01:00Z",
  } as RunEvent;
  act(() => FakeEventSource.instance.emit("run.succeeded", event));

  fireEvent.click(screen.getByRole("tab", { name: "Agent timeline" }));
  expect(await screen.findByText(/#7/)).toBeVisible();
  expect(FakeEventSource.instance.closed).toBe(true);
  await vi.waitFor(() => expect(api.artifacts).toHaveBeenCalledTimes(3));
});

test("labels historical runs that have no recorded artifacts", async () => {
  vi.mocked(api.artifacts).mockResolvedValue([]);
  render(
    <Router initialPath="/runs/run-1">
      <RunDetail />
    </Router>,
  );

  expect(await screen.findByRole("heading", { name: "NVDA" })).toBeVisible();
  fireEvent.click(screen.getByRole("tab", { name: "Deliberation" }));

  expect(
    await screen.findByText(
      "This historical run did not record typed research artifacts.",
    ),
  ).toBeVisible();
});
