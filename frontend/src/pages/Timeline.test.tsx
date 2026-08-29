import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { api, type ResearchNodeView, type TimelineDetail } from "../api/client";
import i18n from "../i18n";
import { Router } from "../router";
import Timeline from "./Timeline";

vi.mock("../api/client", () => ({
  api: {
    timeline: vi.fn(),
    timelines: vi.fn(),
    compareResearchNodes: vi.fn(),
    selectPrimaryCycle: vi.fn(),
    trashRuns: vi.fn(),
    restoreRuns: vi.fn(),
    purgeRuns: vi.fn(),
  },
}));

function node(
  id: string,
  kind: "full" | "incremental",
  date: string,
  rating: "Overweight" | "Hold" = "Overweight",
): ResearchNodeView {
  return {
    id,
    instrument: "NVDA",
    analysis_date: date,
    research_kind: kind,
    full_baseline_run_id: kind === "incremental" ? "full-primary" : null,
    research_schema_version: "1",
    information_cutoff_at: `${date}T21:00:00Z`,
    method_snapshot: { llm_provider: "openai", deep_model: "gpt-5.5" },
    decision: {
      ticker: "NVDA",
      analysis_date: date,
      rating,
      confidence: rating === "Overweight" ? 0.84 : 0.63,
      thesis: kind === "full" ? "Full baseline thesis" : "Incremental thesis changed",
      catalysts: [],
      risks: [],
      invalidation_conditions: [],
    },
    is_active: true,
    is_primary: id === "full-primary",
    is_cycle_head: kind === "incremental",
    cycle_warning: false,
    collection_summary: kind === "incremental" ? { domains: [] } : null,
    research_availability: kind === "incremental" ? { domains: [] } : null,
    information_advancement:
      kind === "incremental"
        ? { advanced: true, reasons: ["New filing materially changes the view."] }
        : null,
    performance: null,
    reassessment: null,
    full_research_required_reasons: [],
  } as unknown as ResearchNodeView;
}

function detail(): TimelineDetail {
  const baseline = node("full-primary", "full", "2026-07-20");
  const increment = node("increment-1", "incremental", "2026-07-25", "Hold");
  return {
    timeline: {
      instrument: "NVDA",
      instrument_name: "NVIDIA Corporation",
      instrument_local_name: "英伟达",
      primary_cycle_id: baseline.id,
      cycles: [{
        id: baseline.id,
        is_primary: true,
        cycle_warning: false,
        head_run_id: increment.id,
        baseline,
        increments: [increment],
      }],
      cycle_total: 1,
      cycle_limit: 12,
      cycle_offset: 0,
      timeline_warning: false,
    },
    primary_cycle_candidates: [],
  } as TimelineDetail;
}

beforeEach(async () => {
  vi.resetAllMocks();
  await i18n.changeLanguage("en");
  vi.mocked(api.timeline).mockResolvedValue(detail());
  vi.mocked(api.timelines).mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
  vi.mocked(api.trashRuns).mockResolvedValue({ runs: [], changed: 1 });
  vi.mocked(api.restoreRuns).mockResolvedValue({ runs: [], changed: 1 });
  vi.mocked(api.purgeRuns).mockResolvedValue({ runs: [], changed: 1 });
});

test("renders name-first timeline cards with research counts and decision context", async () => {
  vi.mocked(api.timelines).mockResolvedValue({
    items: [{
      instrument: "7203.T",
      instrument_name: "Toyota Motor Corporation",
      instrument_local_name: "トヨタ自動車",
      full_cycle_count: 2,
      incremental_node_count: 4,
      latest_analysis_date: "2026-07-25",
      primary_rating: "Overweight",
      primary_confidence: 0.81,
      timeline_warning: true,
    }],
    total: 1,
    limit: 50,
    offset: 0,
  });
  render(<Router initialPath="/timelines"><Timeline /></Router>);

  expect(await screen.findByText("トヨタ自動車")).toBeVisible();
  expect(screen.getByText("Toyota Motor Corporation")).toBeVisible();
  expect(screen.getByText("7203.T")).toBeVisible();
  expect(screen.getByText("81% confidence")).toBeVisible();
  expect(screen.getByRole("link", { name: /トヨタ自動車/ })).toHaveAttribute("href", "/timelines/7203.T");
});

test("renders a Full root and a structurally distinct Incremental child", async () => {
  render(<Router initialPath="/timelines/NVDA"><Timeline /></Router>);

  expect(await screen.findByRole("heading", { name: "英伟达" })).toBeVisible();
  expect(screen.getByText("NVIDIA Corporation")).toBeVisible();
  expect(screen.getByText("Full baseline thesis")).toBeVisible();
  expect(screen.getByText("Incremental thesis changed")).toBeVisible();
  expect(screen.getByText("New filing materially changes the view.")).toBeVisible();
  expect(document.querySelector(".research-node-card.full")).toBeInTheDocument();
  expect(document.querySelector(".research-node-card.incremental")).toBeInTheDocument();
});

test("selects human-readable nodes and renders a structured comparison", async () => {
  vi.mocked(api.compareResearchNodes).mockResolvedValue({
    instrument: "NVDA",
    sides: [
      { node_id: "full-primary", research_kind: "full", analysis_date: "2026-07-20", decision: { rating: "Overweight", confidence: 0.84, thesis: "Full baseline thesis" }, method_snapshot: { llm_provider: "openai", deep_model: "gpt-5.5" } },
      { node_id: "increment-1", research_kind: "incremental", analysis_date: "2026-07-25", decision: { rating: "Hold", confidence: 0.63, thesis: "Incremental thesis changed" }, information_advancement: { reasons: ["Material update"] }, reassessment: { entries: [{ component_id: "earnings", disposition: "weakened", reason: "Margins declined." }] }, performance: { stock: { status: "calculated", calculation: { unrounded_return: 0.12 } }, benchmarks: [] }, method_snapshot: { llm_provider: "openai", deep_model: "gpt-5.5" } },
    ],
    cross_cycle: false,
    method_changed: false,
    warnings: [],
  } as never);
  render(<Router initialPath="/timelines/NVDA"><Timeline /></Router>);

  const selectors = await screen.findAllByRole("button", { name: "Select for comparison" });
  fireEvent.click(selectors[0]);
  fireEvent.click(selectors[1]);
  expect(screen.getByText(/2026-07-25 · Hold/)).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Compare selected nodes" }));

  const comparison = await screen.findByRole("table");
  expect(comparison).toHaveTextContent("Incremental thesis changed");
  expect(comparison).toHaveTextContent("earnings: Weakened");
  expect(comparison).toHaveTextContent("Stock return: 12%");
  expect(comparison).toHaveTextContent("openai / gpt-5.5");
  expect(api.compareResearchNodes).toHaveBeenCalledWith("NVDA", [
    { node_id: "full-primary", lifecycle_state: "active" },
    { node_id: "increment-1", lifecycle_state: "active" },
  ]);
});

test("changes Primary Research using a human-readable cycle", async () => {
  const current = detail();
  const second = node("full-secondary", "full", "2026-07-10", "Hold");
  current.timeline.cycles!.push({ id: second.id, is_primary: false, cycle_warning: false, head_run_id: second.id, baseline: second, increments: [] });
  current.timeline.cycle_total = 2;
  vi.mocked(api.timeline).mockResolvedValue(current);
  vi.mocked(api.selectPrimaryCycle).mockResolvedValue(current);
  render(<Router initialPath="/timelines/NVDA"><Timeline /></Router>);

  fireEvent.click(await screen.findByRole("button", { name: "Make primary" }));
  await waitFor(() => expect(api.selectPrimaryCycle).toHaveBeenCalledWith("NVDA", "full-secondary"));
});

test("requires an explicit Primary replacement when trashing the primary Full cycle", async () => {
  const current = detail();
  const second = node("full-secondary", "full", "2026-07-10", "Hold");
  current.timeline.cycles!.push({ id: second.id, is_primary: false, cycle_warning: false, head_run_id: second.id, baseline: second, increments: [] });
  current.timeline.cycle_total = 2;
  vi.mocked(api.timeline).mockResolvedValue(current);
  render(<Router initialPath="/timelines/NVDA"><Timeline /></Router>);

  fireEvent.click((await screen.findAllByRole("button", { name: "Move Cycle to Trash" }))[0]);
  fireEvent.change(screen.getByLabelText("Replacement Primary Cycle"), { target: { value: "full-secondary" } });
  fireEvent.click(screen.getByRole("button", { name: "Confirm Trash" }));

  await waitFor(() => expect(api.trashRuns).toHaveBeenCalledWith(["full-primary"], { "full-primary": "full-secondary" }));
});

test("paginates complete cycles without using the retired node contract", async () => {
  const current = detail();
  current.timeline.cycle_total = 24;
  vi.mocked(api.timeline).mockResolvedValue(current);
  render(<Router initialPath="/timelines/NVDA"><Timeline /></Router>);

  fireEvent.click(await screen.findByRole("button", { name: "Next →" }));
  await waitFor(() => expect(api.timeline).toHaveBeenLastCalledWith("NVDA", 12, 12, "active"));
});
