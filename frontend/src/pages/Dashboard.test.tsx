import { render, screen } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { api, type Health, type RunPage } from "../api/client";
import i18n from "../i18n";
import { Router } from "../router";
import Dashboard from "./Dashboard";

vi.mock("../api/client", () => ({
  api: {
    health: vi.fn(),
    runs: vi.fn(),
  },
}));

beforeEach(async () => {
  vi.resetAllMocks();
  await i18n.changeLanguage("en");
  vi.mocked(api.health).mockResolvedValue({
    status: "ok",
    database: "ok",
    queue: { queued: 0, running: 0 },
    version: "0.5.0",
  } as Health);
  vi.mocked(api.runs).mockResolvedValue({
    items: [
      {
        id: "run-1",
        source_run_id: null,
        instrument_name: "Toyota Motor Corporation",
        instrument_local_name: "トヨタ自動車",
        research_rating: "Hold",
        research_confidence: 0.78,
        research_kind: "incremental",
        trashed_at: null,
        status: "succeeded",
        request: {
          ticker: "7203.T",
          analysis_date: "2026-07-24",
          asset_type: "stock",
          profile: "standard",
          analysts: ["market"],
          llm_provider: "openai",
          quick_model: "quick",
          deep_model: "deep",
          quick_reasoning_effort: "provider_default",
          deep_reasoning_effort: "provider_default",
          output_language: "en",
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
          node_metrics: {},
        },
        created_at: "2026-07-24T00:00:00Z",
        updated_at: "2026-07-24T00:01:00Z",
      },
    ],
    total: 1,
    limit: 20,
    offset: 0,
  } as RunPage);
});

test("shows local identity and final rating with a run-management entry point", async () => {
  render(
    <Router initialPath="/">
      <Dashboard />
    </Router>,
  );

  expect(await screen.findByText("Toyota Motor Corporation")).toBeVisible();
  expect(screen.getByText("トヨタ自動車")).toBeVisible();
  expect(screen.getByText("7203.T")).toBeVisible();
  expect(screen.getByText("Hold")).toHaveClass("research-rating-badge");
  expect(screen.getByText("78% confidence")).toBeVisible();
  expect(screen.getByText("Incremental research")).toHaveClass(
    "research-kind-badge",
  );
  expect(screen.getByRole("link", { name: "Manage runs" })).toHaveAttribute(
    "href",
    "/runs",
  );
});
