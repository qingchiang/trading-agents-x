import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { api } from "../api/client";
import i18n from "../i18n";
import { Router } from "../router";
import Timeline from "./Timeline";

vi.mock("../api/client", () => ({
  api: { timeline: vi.fn(), timelines: vi.fn(), selectPrimaryCycle: vi.fn() },
}));

beforeEach(async () => {
  vi.resetAllMocks();
  await i18n.changeLanguage("en");
});

test("shows the first Full Run-backed node and keeps its operational Run link", async () => {
  vi.mocked(api.timeline).mockResolvedValue({
    timeline: {
      instrument: "7203.T",
      primary_cycle_id: "run-1",
      nodes: [
        {
          id: "run-1",
          cycle_id: "run-1",
          instrument: "7203.T",
          analysis_date: "2026-07-24",
          research_schema_version: "1",
          information_cutoff_at: "2026-07-24T14:59:59Z",
          method_snapshot: { llm_provider: "fixture" },
          research_kind: "full",
          full_baseline_run_id: null,
          is_cycle_head: true,
          is_primary: true,
          is_active: true,
          trashed_at: null,
        },
      ],
    },
  } as never);

  render(
    <Router initialPath="/timelines/7203.T">
      <Timeline />
    </Router>,
  );

  expect(await screen.findByText("Primary Cycle")).toBeVisible();
  expect(screen.getByText("Full Baseline")).toBeVisible();
  expect(screen.getByText("Cycle Head")).toBeVisible();
  expect(screen.getByText("run-1")).toBeVisible();
  expect(
    screen.getByRole("link", { name: "Open operational Run →" }),
  ).toHaveAttribute("href", "/runs/run-1");
});

test("distinguishes a warned Incremental node without disabling its Timeline", async () => {
  vi.mocked(api.timeline).mockResolvedValue({ timeline: {
    instrument: "NVDA", primary_cycle_id: "full-1", timeline_warning: true,
    nodes: [{ id: "incremental-1", cycle_id: "full-1", instrument: "NVDA",
      analysis_date: "2026-07-24", research_schema_version: "1",
      information_cutoff_at: "2026-07-24T23:59:59Z", method_snapshot: {},
      research_kind: "incremental", full_baseline_run_id: "full-1",
      is_cycle_head: true, is_primary: true, is_active: true, trashed_at: null,
      cycle_warning: true,
      collection_summary: { version: "1", market: "united_states", domains: [{
        domain: "news", source: "fixture", state: "empty", fallback: false,
        retrieved_at: "2026-07-24T20:00:00Z",
        observed_from: "2026-07-24T18:00:00Z",
        observed_through: "2026-07-24T20:00:00Z",
        temporal_bases: ["near_live_advisory"], evidence_refs: [],
      }] },
      research_availability: { version: "1", domains: [{ domain: "news", status: "missing" }] },
      reassessment: { entries: [{ component_id: "thesis", disposition: "reaffirmed", reason: "No new record." }] },
      decision: { rating: "bullish", thesis: "Current complete decision" },
      performance: { stock: { status: "calculated", calculation: {
        provider: "fixture.market", adjustment_basis: "adjusted_close",
        retrieved_at: "2026-07-24T21:00:00Z",
        baseline_information_cutoff_at: "2026-07-21T03:59:59Z",
        target_information_cutoff_at: "2026-07-25T03:59:59Z",
        start_session: "2026-07-20", end_session: "2026-07-24",
        start_value: 100, end_value: 110,
        formula: "(end_value / start_value) - 1", unrounded_return: 0.1,
      } }, benchmarks: [] },
      full_research_required_reasons: [{ code: "required_coverage.news", message: "Required news coverage is missing.", origin: "deterministic" }],
    }],
  } } as never);
  render(<Router initialPath="/timelines/NVDA"><Timeline /></Router>);
  expect(await screen.findByText("Incremental Research Node")).toBeVisible();
  expect(screen.getByText("Full research recommended")).toBeVisible();
  expect(screen.getByText("Collection Summary")).toBeVisible();
  fireEvent.click(screen.getByText("Collection Summary"));
  expect(screen.getByText("2026-07-24T20:00:00Z")).toBeVisible();
  expect(screen.getByText("2026-07-24T18:00:00Z → 2026-07-24T20:00:00Z")).toBeVisible();
  expect(screen.getByText("near_live_advisory")).toBeVisible();
  expect(screen.getByText("Research Availability")).toBeVisible();
  expect(screen.getByText("Reassessment")).toBeVisible();
  expect(screen.getByRole("heading", { name: "Current Decision" })).toBeVisible();
  fireEvent.click(screen.getByText("Performance"));
  expect(screen.getByText("fixture.market")).toBeVisible();
  expect(screen.getByText("adjusted_close")).toBeVisible();
  expect(screen.getByText("100 → 110")).toBeVisible();
  expect(screen.getByText("(end_value / start_value) - 1")).toBeVisible();
  expect(screen.getByText("Cycle warning")).toBeVisible();
});

test("shows evidence-based Information Advancement in the Timeline audit", async () => {
  vi.mocked(api.timeline).mockResolvedValue({ timeline: {
    instrument: "600000.SS", primary_cycle_id: "full-1",
    nodes: [{ id: "incremental-1", cycle_id: "full-1", instrument: "600000.SS",
      analysis_date: "2026-07-24", research_schema_version: "1",
      information_cutoff_at: "2026-07-24T15:59:59Z", method_snapshot: {},
      research_kind: "incremental", full_baseline_run_id: "full-1",
      is_cycle_head: true, is_primary: true, is_active: true, trashed_at: null,
      information_advancement: {
        advanced: true, reasons: ["admissible_observation"],
        newly_reviewable_baseline_component_ids: [],
        observation_ids: ["obs-1"],
      },
    }],
  } } as never);

  render(<Router initialPath="/timelines/600000.SS"><Timeline /></Router>);

  expect(await screen.findByText("Information Advancement")).toBeVisible();
  fireEvent.click(screen.getByText("Information Advancement"));
  expect(screen.getByText("admissible_observation")).toBeVisible();
});

test.each([
  ["NVDA", "ev_0123456789ab"],
  ["7203.T", "ev_123456789abc"],
  ["600000.SS", "ev_abcdef012345"],
])(
  "shows admissible Evidence references in the %s Timeline audit",
  async (instrument, evidenceRef) => {
    vi.mocked(api.timeline).mockResolvedValue({ timeline: {
      instrument, primary_cycle_id: "full-1",
      nodes: [{ id: "incremental-1", cycle_id: "full-1", instrument,
        analysis_date: "2026-07-24", research_schema_version: "1",
        information_cutoff_at: "2026-07-24T15:59:59Z", method_snapshot: {},
        research_kind: "incremental", full_baseline_run_id: "full-1",
        is_cycle_head: true, is_primary: true, is_active: true, trashed_at: null,
        information_advancement: {
          advanced: true, reasons: ["admissible_observation"],
          newly_reviewable_baseline_component_ids: [],
          observation_ids: ["obs-1"],
        },
        collection_summary: { version: "1", market: "united_states", domains: [{
          domain: "news", source: "fixture.news", state: "data", fallback: false,
          retrieved_at: "2026-07-24T20:00:00Z", temporal_bases: ["pit"],
          evidence_refs: [evidenceRef],
        }] },
      }],
    } } as never);

    render(<Router initialPath={`/timelines/${instrument}`}><Timeline /></Router>);

    fireEvent.click(await screen.findByText("Collection Summary"));
    expect(screen.getByText(evidenceRef)).toBeVisible();
  },
);

test("paginates Timeline nodes independently from the Timeline list", async () => {
  vi.mocked(api.timeline)
    .mockResolvedValueOnce({
      timeline: {
        instrument: "7203.T", primary_cycle_id: "run-1", node_total: 21,
        node_limit: 20, node_offset: 0, nodes: [],
      },
    } as never)
    .mockResolvedValueOnce({
      timeline: {
        instrument: "7203.T", primary_cycle_id: "run-1", node_total: 21,
        node_limit: 20, node_offset: 20, nodes: [],
      },
    } as never);

  render(
    <Router initialPath="/timelines/7203.T">
      <Timeline />
    </Router>,
  );

  expect(await screen.findByRole("button", { name: "Next →" })).toBeEnabled();
  expect(api.timeline).toHaveBeenCalledWith("7203.T", 20, 0);
  fireEvent.click(screen.getByRole("button", { name: "Next →" }));
  await waitFor(() => expect(api.timeline).toHaveBeenCalledWith("7203.T", 20, 20));
});

test("lists derived timelines without presenting Execution History as a timeline", async () => {
  vi.mocked(api.timelines).mockResolvedValue({
    items: [{ instrument: "7203.T", primary_cycle_id: "run-1", node_count: 1 }],
    total: 1,
  } as never);

  render(
    <Router initialPath="/timelines">
      <Timeline />
    </Router>,
  );

  expect(await screen.findByRole("heading", { name: "Research Timelines" })).toBeVisible();
  expect(screen.getByRole("link", { name: "7203.T" })).toHaveAttribute(
    "href",
    "/timelines/7203.T",
  );
  expect(screen.getByText("1 research node")).toBeVisible();
});

test("lets the user select a different Full Cycle as Primary Research", async () => {
  vi.mocked(api.timeline).mockResolvedValue({
    timeline: {
      instrument: "7203.T",
      primary_cycle_id: "run-1",
      nodes: [
        {
          id: "run-1", cycle_id: "run-1", instrument: "7203.T",
          analysis_date: "2026-07-24", research_schema_version: "1",
          information_cutoff_at: "2026-07-24T14:59:59Z", method_snapshot: {},
          research_kind: "full", full_baseline_run_id: null, is_cycle_head: true,
          is_primary: true, is_active: true, trashed_at: null,
        },
        {
          id: "run-2", cycle_id: "run-2", instrument: "7203.T",
          analysis_date: "2026-07-24", research_schema_version: "1",
          information_cutoff_at: "2026-07-24T14:59:59Z", method_snapshot: {},
          research_kind: "full", full_baseline_run_id: null, is_cycle_head: true,
          is_primary: false, is_active: true, trashed_at: null,
        },
      ],
    },
  } as never);
  vi.mocked(api.selectPrimaryCycle).mockResolvedValue({
    timeline: {
      instrument: "7203.T", primary_cycle_id: "run-2", nodes: [],
    },
  } as never);

  render(
    <Router initialPath="/timelines/7203.T">
      <Timeline />
    </Router>,
  );

  fireEvent.click(await screen.findByRole("button", { name: "Make primary" }));
  await waitFor(() =>
    expect(api.selectPrimaryCycle).toHaveBeenCalledWith("7203.T", "run-2"),
  );
});
