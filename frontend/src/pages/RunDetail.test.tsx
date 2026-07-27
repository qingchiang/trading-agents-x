import { act, render, screen } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import {
  api,
  type RunDetail as RunDetailType,
  type RunEvent,
} from "../api/client";
import i18n from "../i18n";
import { Router } from "../router";
import RunDetail from "./RunDetail";

vi.mock("../api/client", () => ({
  api: {
    run: vi.fn(),
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
        warnings: ["Historical source was partial."],
        narrative: "# Market report\n\nEvidence-grounded narrative.",
      },
    },
    decision: {
      rating: "Hold",
      confidence: 0.65,
      thesis: "Evidence is balanced.",
      evidence_refs: ["ev_0123456789ab"],
      catalysts: [],
      risks: ["Demand slows"],
      invalidation_conditions: ["New filing changes the thesis"],
      time_horizon: "6-12 months",
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

beforeEach(async () => {
  vi.resetAllMocks();
  await i18n.changeLanguage("en");
  vi.mocked(api.run).mockResolvedValue(detail);
  vi.stubGlobal("EventSource", FakeEventSource);
});

test("renders typed reports, provenance warnings, metrics, and replayed events", async () => {
  render(
    <Router initialPath="/runs/run-1">
      <RunDetail />
    </Router>,
  );

  expect(await screen.findByRole("heading", { name: "NVDA" })).toBeVisible();
  expect(
    await screen.findByText("Historical source was partial."),
  ).toBeVisible();
  expect(await screen.findByText("ev_0123456789ab")).toBeVisible();
  expect(screen.getByText("1,200")).toBeVisible();

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

  expect(await screen.findByText(/#7/)).toBeVisible();
  expect(FakeEventSource.instance.closed).toBe(true);
});
