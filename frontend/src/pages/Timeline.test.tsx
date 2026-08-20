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
      outcome_review_status: "omitted", cycle_warning: true,
      collection_manifest: { entries: [{ domain: "news", source: "fixture", outcome: "complete_empty", source_watermark: "fixture-watermark" }] },
      research_coverage: { domains: [{ domain: "news", requirement: "required", status: "missing" }] },
      reassessment: { entries: [{ component_id: "thesis", disposition: "reaffirmed", reason: "No new record." }] },
      decision: { rating: "bullish", thesis: "Current complete decision" },
      performance: { status: "not_yet_observable", reason: "No completed interval." },
      full_research_required_reasons: [{ code: "required_coverage.news", message: "Required news coverage is missing.", origin: "deterministic" }],
    }],
  } } as never);
  render(<Router initialPath="/timelines/NVDA"><Timeline /></Router>);
  expect(await screen.findByText("Incremental Research Node")).toBeVisible();
  expect(screen.getByText("Full research recommended")).toBeVisible();
  expect(screen.getByText("Outcome review omitted")).toBeVisible();
  expect(screen.getByText("Incremental Manifest")).toBeVisible();
  expect(screen.getByText("Coverage")).toBeVisible();
  expect(screen.getByText("Reassessment")).toBeVisible();
  expect(screen.getByRole("heading", { name: "Current Decision" })).toBeVisible();
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
        advanced: true, reasons: ["admissible_evidence"],
        newly_reviewable_baseline_component_ids: [],
      },
    }],
  } } as never);

  render(<Router initialPath="/timelines/600000.SS"><Timeline /></Router>);

  expect(await screen.findByText("Information Advancement")).toBeVisible();
  fireEvent.click(screen.getByText("Information Advancement"));
  expect(screen.getByText("admissible_evidence")).toBeVisible();
});

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
