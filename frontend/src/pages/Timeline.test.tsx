import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import {
  api,
  type ResearchNodeComparison,
  type ResearchNodeView,
  type TimelineDetail,
} from "../api/client";
import i18n from "../i18n";
import { Router } from "../router";
import Timeline from "./Timeline";

vi.mock("../api/client", () => ({
  api: {
    run: vi.fn(),
    analysisCutoffContext: vi.fn(),
    evidence: vi.fn(),
    artifacts: vi.fn(),
    timeline: vi.fn(),
    timelines: vi.fn(),
    compareResearchNodes: vi.fn(),
    selectPrimaryCycle: vi.fn(),
    trashRuns: vi.fn(),
    previewLifecycle: vi.fn(),
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
    research_schema_version: "2",
    information_cutoff_at: `${date}T21:00:00Z`,
    method_snapshot: { llm_provider: "openai", deep_model: "gpt-5.5" },
    decision: {
      ticker: "NVDA",
      analysis_date: date,
      rating,
      confidence: rating === "Overweight" ? "high" : "medium",
      thesis: kind === "full" ? "Full baseline thesis" : "Incremental thesis changed",
      catalysts: [],
      risks: [],
      invalidation_conditions: [],
    },
    is_active: true,
    is_primary: id === "full-primary",
    is_cycle_head: kind === "incremental",
    cycle_warning: false,
    collection_summary:
      kind === "incremental"
        ? {
            market: "united_states",
            version: "1",
            domains: [
              {
                domain: "news",
                state: "partial",
                diagnostic: { code: "coverage.partial" },
                sources: [
                  {
                    source: "sec",
                    retrieved_at: `${date}T20:00:00Z`,
                  },
                ],
              },
            ],
          }
        : null,
    research_availability: kind === "incremental" ? { domains: [] } : null,
    information_advancement:
      kind === "incremental"
        ? { advanced: true, reasons: ["admissible_observation"] }
        : null,
    performance:
      kind === "incremental"
        ? {
            stock: {
              status: "calculated",
              calculation: {
                adjustment_basis: "split adjusted",
                baseline_information_cutoff_at: "2026-07-20T21:00:00Z",
                end_session: date,
                end_value: 105,
                formula: "(end_value / start_value) - 1",
                provider: "test",
                retrieved_at: `${date}T22:00:00Z`,
                start_session: "2026-07-20",
                start_value: 100,
                target_information_cutoff_at: `${date}T21:00:00Z`,
                unrounded_return: 0.05,
              },
            },
            benchmarks: [],
          }
        : null,
    reassessment:
      kind === "incremental"
        ? {
            entries: [
              {
                component_id: "thesis",
                disposition: "weakened",
                reason: "The new filing weakens the baseline thesis.",
                evidence_refs: [],
              },
              {
                component_id: "risks.0",
                disposition: "reaffirmed",
                reason: "The existing risk remains.",
                evidence_refs: [],
              },
            ],
          }
        : null,
    decision_outcome: kind === "incremental" ? "updated" : null,
    decision_outcome_reason:
      kind === "incremental" ? "The Decision thesis changed." : null,
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
      active_full_cycles: [{
        id: baseline.id,
        analysis_date: baseline.analysis_date,
        is_primary: true,
        rating: baseline.decision?.rating,
        confidence: baseline.decision?.confidence,
      }],
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
  vi.stubGlobal("ResizeObserver", class { constructor(private callback: ResizeObserverCallback) {} observe() { this.callback([{ contentRect: { width: 1440 } } as ResizeObserverEntry], this as unknown as ResizeObserver); } disconnect() {} });
  vi.resetAllMocks();
  Object.defineProperty(window, "innerWidth", { value: 1440, configurable: true });
  await i18n.changeLanguage("en");
  vi.mocked(api.previewLifecycle).mockImplementation(async (ids, action) => ({ action, affected_run_ids: ids, affected_runs: [], blocked_reasons: [], primary_replacements: { "full-primary": [{ id: "full-secondary", analysis_date: "2026-07-10", rating: "Hold" }, { id: "full-off-page", analysis_date: "2026-06-30", rating: "Hold" }] } }));
  vi.mocked(api.analysisCutoffContext).mockResolvedValue({ max_analysis_date: "2026-07-26", observed_at: "2026-07-26T00:00:00Z", valid_until: "2999-01-01T00:00:00Z" } as never);
  vi.mocked(api.evidence).mockRejectedValue(new Error("Unavailable"));
  vi.mocked(api.run).mockImplementation(async id => ({
    run: { id, research_kind: id.startsWith("increment") ? "incremental" : "full", status: "succeeded", attempt: 1,
      request: { ticker: "NVDA", analysis_date: "2026-07-25", analysts: [] }, metrics: {} },
    result: null, attempts: [], evidence_status: { status: "pending" },
  }) as never);
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
      primary_confidence: "high",
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
  expect(screen.getByText("High confidence")).toBeVisible();
  expect(screen.getByRole("link", { name: /トヨタ自動車/ })).toHaveAttribute("href", "/timelines/7203.T");
});

test("selects the primary head and switches research inside the instrument workspace", async () => {
  render(<Router initialPath="/timelines/NVDA"><Timeline /></Router>);
  expect(await screen.findByRole("heading", { name: "英伟达" })).toBeVisible();
  await waitFor(() => expect(api.run).toHaveBeenCalledWith("increment-1"));
  const history = screen.getByRole("navigation", { name: "Research history" });
  fireEvent.click(within(history).getByRole("button", { name: /^2026-07-20/ }));
  await waitFor(() => expect(api.run).toHaveBeenCalledWith("full-primary"));
  expect(api.selectPrimaryCycle).not.toHaveBeenCalled();
  expect(screen.queryByText("Audit details")).not.toBeInTheDocument();
});

test("does not substitute the primary head for an unavailable explicit node", async () => {
  render(<Router initialPath="/timelines/NVDA?node=missing"><Timeline /></Router>);
  expect(await screen.findByText(/This research is unavailable/)).toBeVisible();
  expect(api.run).not.toHaveBeenCalled();
});

test("selects human-readable nodes and renders a structured comparison", async () => {
  const comparison: ResearchNodeComparison = {
    instrument: "NVDA",
    sides: [
      {
        node_id: "full-primary",
        cycle_id: "full-primary",
        lifecycle_state: "active",
        research_kind: "full",
        research_schema_version: "2",
        analysis_date: "2026-07-20",
        decision: {
          rating: "Overweight",
          confidence: "high",
          thesis: "Full baseline thesis",
        },
        method_snapshot: { llm_provider: "openai", deep_model: "gpt-5.4" },
      },
      {
        node_id: "increment-1",
        cycle_id: "full-primary",
        lifecycle_state: "active",
        research_kind: "incremental",
        research_schema_version: "2",
        analysis_date: "2026-07-25",
        decision: {
          rating: "Hold",
          confidence: "medium",
          thesis: "Incremental thesis changed",
        },
        information_advancement: { advanced: true, reasons: ["admissible_observation"] },
        decision_outcome: "updated",
        decision_outcome_reason: "The Decision thesis changed.",
        reassessment: {
          entries: [
            {
              component_id: "earnings",
              disposition: "weakened",
              reason: "Margins declined.",
            },
          ],
        },
        performance: {
          stock: {
            status: "calculated",
            calculation: {
              adjustment_basis: "split adjusted",
              baseline_information_cutoff_at: "2026-07-20T21:00:00Z",
              end_session: "2026-07-25",
              end_value: 112,
              formula: "(end_value / start_value) - 1",
              provider: "test",
              retrieved_at: "2026-07-25T22:00:00Z",
              start_session: "2026-07-20",
              start_value: 100,
              target_information_cutoff_at: "2026-07-25T21:00:00Z",
              unrounded_return: 0.12,
            },
          },
          benchmarks: [],
        },
        full_research_required_reasons: [
          {
            code: "evidence.material_conflict",
            message: "Refresh the complete baseline.",
            origin: "semantic",
            evidence_refs: [],
          },
        ],
        method_snapshot: { llm_provider: "openai", deep_model: "gpt-5.5" },
      },
    ],
    decision_sections: [
      {
        key: "rating",
        values: [
          { state: "recorded", value: "Overweight" },
          { state: "recorded", value: "Hold" },
        ],
      },
      {
        key: "thesis",
        values: [
          { state: "recorded", value: "Full baseline thesis" },
          { state: "recorded", value: "Incremental thesis changed" },
        ],
      },
      {
        key: "confidence",
        values: [
          { state: "recorded", value: "high" },
          { state: "recorded", value: "high" },
        ],
      },
      {
        key: "executive_summary",
        values: [
          { state: "not_recorded_under_this_schema" },
          { state: "recorded", value: "New summary" },
        ],
      },
      {
        key: "valuation_assessment",
        values: [
          { state: "null" },
          { state: "empty", value: {} },
        ],
      },
      {
        key: "scenarios",
        values: [
          {
            state: "recorded",
            value: [{ kind: "base", outcome: "Baseline outcome" }],
          },
          {
            state: "recorded",
            value: [{ kind: "base", outcome: "Updated outcome" }],
          },
        ],
      },
    ],
    cross_cycle: false,
    method_changed: false,
    warnings: [],
  };
  vi.mocked(api.compareResearchNodes).mockResolvedValue(comparison);
  render(<Router initialPath="/timelines/NVDA"><Timeline /></Router>);

  fireEvent.click(await screen.findByRole("button", { name: "More" }));
  fireEvent.click(await screen.findByRole("button", { name: "Compare research" }));
  const selectors = await screen.findAllByRole("checkbox", { name: /Select for comparison/ });
  fireEvent.click(selectors[0]);
  fireEvent.click(selectors[1]);
  expect(screen.getAllByText("2026-07-25").length).toBeGreaterThan(0);
  const compareButton = screen.getByRole("button", {
    name: "Compare selected nodes",
  });
  compareButton.focus();
  fireEvent.click(compareButton);

  const dialog = await screen.findByRole("region", { name: "Node Comparison" }, { timeout: 3000 });
  expect(document.body.style.overflow).not.toBe("hidden");
  expect(within(dialog).getByText("Incremental thesis changed")).toBeVisible();
  expect(within(dialog).queryByText("High confidence")).not.toBeInTheDocument();

  expect(within(dialog).getAllByText("Not Recorded Under This Schema")[0]).toBeVisible();
  expect(within(dialog).getByText("Null")).toBeVisible();
  expect(within(dialog).getByText("Empty")).toBeVisible();
  expect(within(dialog).getByText("Updated outcome")).toBeVisible();

  expect(within(dialog).getByText(/Weakened/)).toBeVisible();
  expect(within(dialog).queryByText(/openai \/ gpt-5.5/)).toBeNull();
  expect(within(dialog).getAllByRole("link", { name: /Run & diagnostics/ })).toHaveLength(2);
  expect(within(dialog).getByText(/Stock return: 12%/)).toBeVisible();
  expect(
    within(dialog).getByText("Refresh the complete baseline."),
  ).toBeVisible();

  fireEvent.click(
    within(dialog).getByRole("checkbox", {
      name: "Show changed sections only",
    }),
  );
  expect(within(dialog).getAllByText("High confidence")).toHaveLength(2);

  fireEvent.click(within(dialog).getByRole("button", { name: "Swap sides" }));
  await waitFor(() => expect(api.compareResearchNodes).toHaveBeenLastCalledWith("NVDA", [
    { node_id: "increment-1", lifecycle_state: "active" },
    { node_id: "full-primary", lifecycle_state: "active" },
  ]));
  fireEvent.click(within(await screen.findByRole("region", { name: "Node Comparison" })).getByRole("button", { name: "Close" }));
  expect(screen.queryByRole("region", { name: "Node Comparison" })).not.toBeInTheDocument();
  expect(document.body.style.overflow).not.toBe("hidden");
});

test("changes Primary Research using a human-readable cycle", async () => {
  const current = detail();
  const second = node("full-secondary", "full", "2026-07-10", "Hold");
  current.timeline.cycles!.push({ id: second.id, is_primary: false, cycle_warning: false, head_run_id: second.id, baseline: second, increments: [] });
  current.timeline.active_full_cycles!.push({ id: second.id, analysis_date: second.analysis_date, is_primary: false, rating: second.decision?.rating, confidence: second.decision?.confidence });
  current.timeline.cycle_total = 2;
  vi.mocked(api.timeline).mockResolvedValue(current);
  vi.mocked(api.selectPrimaryCycle).mockResolvedValue(current);
  render(<Router initialPath="/timelines/NVDA"><Timeline /></Router>);

  fireEvent.click(await screen.findByRole("button", { name: /Expand cycle.*2026-07-10/ }));
  const target = (await screen.findByRole("button", { name: /^2026-07-10/ })).closest(".history-node")!;
  fireEvent.click(within(target as HTMLElement).getByRole("button", { name: "Manage" }));
  fireEvent.click(screen.getByRole("button", { name: "Make primary" }));
  await waitFor(() => expect(api.selectPrimaryCycle).toHaveBeenCalledWith("NVDA", "full-secondary"));
});

test("requires an explicit Primary replacement when trashing the primary Full cycle", async () => {
  const current = detail();
  const second = node("full-secondary", "full", "2026-07-10", "Hold");
  current.timeline.cycles!.push({ id: second.id, is_primary: false, cycle_warning: false, head_run_id: second.id, baseline: second, increments: [] });
  current.timeline.active_full_cycles!.push({ id: second.id, analysis_date: second.analysis_date, is_primary: false, rating: second.decision?.rating, confidence: second.decision?.confidence });
  current.timeline.cycle_total = 2;
  vi.mocked(api.timeline).mockResolvedValue(current);
  render(<Router initialPath="/timelines/NVDA"><Timeline /></Router>);

  const target = (await screen.findByRole("button", { name: /^2026-07-20/ })).closest(".history-node")!;
  fireEvent.click(within(target as HTMLElement).getByRole("button", { name: "Manage" }));
  fireEvent.click(screen.getByRole("button", { name: "Move Cycle to Trash" }));
  fireEvent.change(await screen.findByLabelText("Replacement Primary Cycle"), { target: { value: "full-secondary" } });
  fireEvent.click(screen.getByRole("button", { name: "Confirm Trash" }));

  await waitFor(() => expect(api.trashRuns).toHaveBeenCalledWith(["full-primary"], { "full-primary": "full-secondary" }, ["full-primary"]));
});

test("offers Primary replacement cycles outside the current Timeline page", async () => {
  const current = detail();
  current.timeline.cycle_total = 13;
  current.timeline.active_full_cycles = [
    ...(current.timeline.active_full_cycles ?? []),
    {
      id: "full-off-page",
      analysis_date: "2026-06-30",
      is_primary: false,
      rating: "Hold",
      confidence: "medium",
    },
  ];
  vi.mocked(api.timeline).mockResolvedValue(current);
  render(<Router initialPath="/timelines/NVDA"><Timeline /></Router>);

  const target = (await screen.findByRole("button", { name: /^2026-07-20/ })).closest(".history-node")!;
  fireEvent.click(within(target as HTMLElement).getByRole("button", { name: "Manage" }));
  fireEvent.click(screen.getByRole("button", { name: "Move Cycle to Trash" }));
  expect(
    await screen.findByRole("option", { name: "2026-06-30 · Hold" }),
  ).toBeVisible();
});

test("paginates complete cycles without using the retired node contract", async () => {
  const current = detail();
  current.timeline.cycle_total = 24;
  vi.mocked(api.timeline).mockImplementation(async (_instrument, _limit, offset) => ({ timeline: { ...current.timeline, cycle_offset: offset } }));
  render(<Router initialPath="/timelines/NVDA"><Timeline /></Router>);

  fireEvent.click(await screen.findByRole("button", { name: "Next" }));
  await waitFor(() => expect(api.timeline).toHaveBeenLastCalledWith("NVDA", 12, 12, "active", undefined));
});
