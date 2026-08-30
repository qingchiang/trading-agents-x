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
                formula: "(end / start) - 1",
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
  expect(screen.getByText("New admissible observation")).toBeVisible();
  expect(screen.getByText("Stock return: 5%")).toBeVisible();
  expect(screen.getByText("1 non-reaffirmed item")).toBeVisible();
  fireEvent.click(screen.getByText("Update details"));
  expect(screen.getByText("Collection Summary")).toBeVisible();
  expect(screen.getByText("News")).toBeVisible();
  expect(screen.getByText("Partial")).toBeVisible();
  expect(screen.getByText("sec")).toBeVisible();
  const incrementalCard = document.querySelector<HTMLElement>(
    ".research-node-card.incremental",
  );
  expect(incrementalCard).not.toBeNull();
  expect(within(incrementalCard!).getAllByText("Audit details")).toHaveLength(1);
  expect(within(incrementalCard!).getByText(/coverage\.partial/)).not.toBeVisible();
  fireEvent.click(within(incrementalCard!).getAllByText("Audit details")[0]);
  expect(screen.getByText(/coverage\.partial/)).toBeVisible();
  expect(document.querySelector(".research-node-card.full")).toBeInTheDocument();
  expect(document.querySelector(".research-node-card.incremental")).toBeInTheDocument();
});

test.each([
  [
    "en",
    "Stock return: Not yet observable · Both cutoffs resolve to the same completed session.",
    "S&P 500: Unavailable · Benchmark market data is unavailable.",
  ],
  [
    "zh-CN",
    "股票回报: 尚不可观察 · 两个截止点对应同一个已完成交易日。",
    "S&P 500: 不可用 · 基准市场数据不可用。",
  ],
  [
    "ja",
    "株価リターン: まだ観測不可 · 両方のカットオフは同じ完了済みセッションに対応しています。",
    "S&P 500: 利用不可 · ベンチマークの市場データを利用できません。",
  ],
])(
  "localizes stock and benchmark performance reasons in %s",
  async (language, knownReason, fallback) => {
    const current = detail();
    current.timeline.cycles![0].increments![0].performance = {
      stock: {
        status: "not_yet_observable",
        reason: "Both cutoffs resolve to the same completed session.",
      },
      benchmarks: [
        {
          name: "S&P 500",
          component: {
            status: "unavailable",
            reason: "Benchmark unavailable: calendar.unavailable.",
          },
        },
      ],
    } as never;
    await i18n.changeLanguage(language);
    vi.mocked(api.timeline).mockResolvedValue(current);

    render(<Router initialPath="/timelines/NVDA"><Timeline /></Router>);

    expect(await screen.findByText(knownReason)).toBeVisible();
    expect(screen.getByText(fallback)).toBeVisible();
    expect(screen.queryByText("calendar.unavailable")).not.toBeInTheDocument();
  },
);

test("selects human-readable nodes and renders a structured comparison", async () => {
  const comparison: ResearchNodeComparison = {
    instrument: "NVDA",
    sides: [
      {
        node_id: "full-primary",
        cycle_id: "full-primary",
        lifecycle_state: "active",
        research_kind: "full",
        research_schema_version: "1",
        analysis_date: "2026-07-20",
        decision: {
          rating: "Overweight",
          confidence: 0.84,
          thesis: "Full baseline thesis",
        },
        method_snapshot: { llm_provider: "openai", deep_model: "gpt-5.4" },
      },
      {
        node_id: "increment-1",
        cycle_id: "full-primary",
        lifecycle_state: "active",
        research_kind: "incremental",
        research_schema_version: "1",
        analysis_date: "2026-07-25",
        decision: {
          rating: "Hold",
          confidence: 0.63,
          thesis: "Incremental thesis changed",
        },
        information_advancement: { advanced: true, reasons: ["admissible_observation"] },
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
              formula: "(end / start) - 1",
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
          { state: "recorded", value: 0.84 },
          { state: "recorded", value: 0.84 },
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

  const selectors = await screen.findAllByRole("button", { name: "Select for comparison" });
  fireEvent.click(selectors[0]);
  fireEvent.click(selectors[1]);
  expect(screen.getByText(/2026-07-25 · Hold/)).toBeVisible();
  const compareButton = screen.getByRole("button", {
    name: "Compare selected nodes",
  });
  compareButton.focus();
  fireEvent.click(compareButton);

  const dialog = await screen.findByRole("dialog", { name: "Node Comparison" });
  expect(document.body.style.overflow).toBe("hidden");
  expect(within(dialog).getByText("Incremental thesis changed")).toBeVisible();
  expect(within(dialog).queryByText("84% confidence")).not.toBeInTheDocument();

  fireEvent.click(within(dialog).getByText("Extended conclusions"));
  expect(within(dialog).getByText("Not Recorded Under This Schema")).toBeVisible();
  expect(within(dialog).getByText("Null")).toBeVisible();
  expect(within(dialog).getByText("Empty")).toBeVisible();
  expect(within(dialog).getByText("Updated outcome")).toBeVisible();

  fireEvent.click(within(dialog).getByText("Update audit"));
  expect(within(dialog).getByText(/earnings: Weakened/)).toBeVisible();
  expect(within(dialog).getByText(/openai \/ gpt-5.5/)).toBeVisible();
  expect(within(dialog).getByText(/Stock return: 12%/)).toBeVisible();
  expect(
    within(dialog).getByText("Refresh the complete baseline."),
  ).toBeVisible();

  fireEvent.click(
    within(dialog).getByRole("checkbox", {
      name: "Show changed sections only",
    }),
  );
  expect(within(dialog).getAllByText("84% confidence")).toHaveLength(2);

  let headers = within(dialog).getAllByRole("columnheader");
  expect(headers[1]).toHaveTextContent("Full research");
  expect(headers[2]).toHaveTextContent("Incremental research");
  fireEvent.click(within(dialog).getByRole("button", { name: "Swap sides" }));
  headers = within(dialog).getAllByRole("columnheader");
  expect(headers[1]).toHaveTextContent("Incremental research");
  expect(headers[2]).toHaveTextContent("Full research");
  expect(api.compareResearchNodes).toHaveBeenCalledTimes(1);
  expect(api.compareResearchNodes).toHaveBeenCalledWith("NVDA", [
    { node_id: "full-primary", lifecycle_state: "active" },
    { node_id: "increment-1", lifecycle_state: "active" },
  ]);

  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("dialog", { name: "Node Comparison" })).not.toBeInTheDocument();
  expect(document.body.style.overflow).toBe("");
  expect(compareButton).toHaveFocus();
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

  fireEvent.click(await screen.findByRole("button", { name: "Make primary" }));
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

  fireEvent.click((await screen.findAllByRole("button", { name: "Move Cycle to Trash" }))[0]);
  fireEvent.change(screen.getByLabelText("Replacement Primary Cycle"), { target: { value: "full-secondary" } });
  fireEvent.click(screen.getByRole("button", { name: "Confirm Trash" }));

  await waitFor(() => expect(api.trashRuns).toHaveBeenCalledWith(["full-primary"], { "full-primary": "full-secondary" }));
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
      confidence: 0.58,
    },
  ];
  vi.mocked(api.timeline).mockResolvedValue(current);
  render(<Router initialPath="/timelines/NVDA"><Timeline /></Router>);

  fireEvent.click(await screen.findByRole("button", { name: "Move Cycle to Trash" }));
  expect(
    screen.getByRole("option", { name: "2026-06-30 · Hold · 58%" }),
  ).toBeVisible();
});

test("paginates complete cycles without using the retired node contract", async () => {
  const current = detail();
  current.timeline.cycle_total = 24;
  vi.mocked(api.timeline).mockResolvedValue(current);
  render(<Router initialPath="/timelines/NVDA"><Timeline /></Router>);

  fireEvent.click(await screen.findByRole("button", { name: "Next →" }));
  await waitFor(() => expect(api.timeline).toHaveBeenLastCalledWith("NVDA", 12, 12, "active"));
});
