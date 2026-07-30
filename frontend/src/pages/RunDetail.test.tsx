import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import {
  api,
  type Capabilities,
  type ResearchArtifact,
  type RunDetail as RunDetailType,
  type RunEvent,
} from "../api/client";
import i18n from "../i18n";
import { Router, useLocation } from "../router";
import RunDetail from "./RunDetail";

vi.mock("../api/client", () => ({
  api: {
    run: vi.fn(),
    artifacts: vi.fn(),
    action: vi.fn(),
    capabilities: vi.fn(),
    restoreRuns: vi.fn(),
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

function analystReport(
  analyst: "fundamentals" | "market" | "news" | "social",
  title: string,
  warnings: unknown[] = [],
) {
  return {
    analyst,
    executive_summary: `${title} summary`,
    confidence: 0.7,
    claims: [
      {
        id: `${analyst}.claim_1`,
        kind: "inference",
        statement: "Evidence is mixed.",
        implication: "The conclusion should preserve uncertainty.",
        confidence: 0.7,
        evidence_refs: ["ev_0123456789ab"],
      },
    ],
    sections: [
      {
        id: "overview",
        title: `${title} report`,
        narrative: "Evidence-grounded narrative.",
        table_ids: [],
      },
    ],
    tables: [],
    catalysts: [],
    risks: ["Evidence may deteriorate."],
    invalidation_conditions: ["New evidence contradicts the report."],
    evidence_refs: ["ev_0123456789ab"],
    warnings,
  };
}

function LocationProbe() {
  const location = useLocation();
  return (
    <output data-testid="router-location">
      {location.pathname}
      {location.search}
      {location.hash}
    </output>
  );
}

const detail = {
  run: {
    id: "run-1",
    source_run_id: null,
    instrument_name: "NVIDIA Corporation",
    trashed_at: null,
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
  result: {
    run_id: "run-1",
    status: "succeeded",
    instrument: "NVDA",
    reports: {
      social: analystReport("social", "Social"),
      news: analystReport("news", "News"),
      market: {
        ...analystReport("market", "Market", [
          {
            code: "evidence.degraded",
            message: "Historical source was partial.",
            evidence_ref: "ev_0123456789ab",
            source: "fixture",
          },
        ]),
        evidence_refs: [
          "ev_0123456789ab",
          "ev_fedcba987654",
        ],
        sections: [
          {
            id: "overview",
            title: "Market report",
            narrative:
              "Compare ev_0123456789ab with ev_fedcba987654.",
            table_ids: [],
          },
        ],
      },
      fundamentals: analystReport("fundamentals", "Fundamentals"),
    },
    decision: {
      rating: "Hold",
      confidence: 0.65,
      executive_summary: "Balanced research summary.",
      thesis: "Evidence is balanced.",
      evidence_refs: ["ev_0123456789ab"],
      memory_refs: ["memory:legacy-run"],
      catalysts: [],
      risks: ["Demand slows"],
      invalidation_conditions: ["New filing changes the thesis"],
      unresolved_questions: [],
      time_horizon: "6-12 months",
      scenarios: (["base", "bull", "bear"] as const).map((kind) => ({
        kind,
        core_assumptions: ["Current evidence remains representative."],
        outcome: `${kind} outcome.`,
        evidence_refs: ["ev_0123456789ab"],
      })),
    },
    evidence: {
      version: "4",
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
        {
          ref: "ev_fedcba987654",
          source: "alternate-fixture",
          evidence_type: "Composite snapshot",
          requested_date: "2026-07-24",
          effective_date: "2026-07-24",
          available_at: "2026-07-24T00:00:01Z",
          content: "**Close:** 100 USD",
          value: 100,
          unit: "USD",
          quality: "low",
          fallback: true,
          provenance: { vendor: "alternate-feed" },
        },
      ],
    },
    metrics: {
      llm_calls: 4,
      tool_calls: 3,
      input_tokens: 1200,
      output_tokens: 400,
      wall_time_seconds: 12.4,
      node_metrics: {
        "analyst.market": {
          llm_calls: 2,
          tool_calls: 3,
          input_tokens: 800,
          output_tokens: 220,
          wall_time_seconds: 2.1,
        },
        "committee.final": {
          llm_calls: 2,
          tool_calls: 0,
          input_tokens: 400,
          output_tokens: 180,
          wall_time_seconds: 4.5,
        },
      },
    },
    warnings: [
      {
        code: "structured_output.recovered",
        message: "One structured output required recovery.",
        evidence_ref: null,
        source: "committee.final",
      },
    ],
  },
} as unknown as RunDetailType;

const artifacts = [
  {
    id: "artifact-bull",
    run_id: "run-1",
    attempt: 1,
    stage: "case",
    role: "bull",
    round: 0,
    schema_version: "1",
    generation_method: "tool_call",
    created_at: "2026-07-24T00:00:40Z",
    content: {
      role: "bull",
      executive_summary: "Constructive case summary.",
      thesis: "**Demand** remains constructive.",
      arguments: [
        {
          id: "case.bull.argument_1",
          claim_ids: ["market.claim_1"],
          statement: "Demand remains constructive.",
          mechanism: "Demand supports operating leverage.",
          implication: "The constructive case remains viable.",
          confidence: 0.6,
          evidence_refs: ["ev_0123456789ab"],
        },
      ],
      strongest_counterarguments: [
        "Valuation risk is already reflected.",
      ],
      fragile_assumptions: ["Demand remains resilient."],
      evidence_refs: ["ev_0123456789ab"],
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
    generation_method: "tool_call",
    created_at: "2026-07-24T00:00:50Z",
    content: {
      preliminary_rating: "Hold",
      confidence: 0.62,
      executive_summary: "Balanced judge draft.",
      thesis: "The judge draft remains balanced.",
      rulings: [
        {
          agenda_id: "debate.issue_1",
          resolution: "mixed",
          rationale: "Both cases retain support.",
          accepted_claim_ids: ["market.claim_1"],
          rejected_claim_ids: [],
          evidence_refs: ["ev_0123456789ab"],
        },
      ],
      evidence_refs: ["ev_0123456789ab"],
      memory_refs: [],
      catalysts: [],
      risks: ["Demand slows"],
      invalidation_conditions: ["New evidence supersedes the snapshot"],
      unresolved_questions: ["Which scenario dominates?"],
      time_horizon: "6-12 months",
    },
  },
] as unknown as ResearchArtifact[];

beforeEach(async () => {
  vi.resetAllMocks();
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
  });
  localStorage.removeItem("tradingagents-timeline-order");
  localStorage.removeItem("tradingagents-audit-details-open");
  await i18n.changeLanguage("en");
  vi.mocked(api.run).mockResolvedValue(detail);
  vi.mocked(api.artifacts).mockResolvedValue(artifacts);
  vi.mocked(api.capabilities).mockResolvedValue({
    defaults: { trash_retention_days: 30 },
  } as Capabilities);
  vi.stubGlobal("EventSource", FakeEventSource);
});

test("restores deliberation and resolves evidence references across run views", async () => {
  render(
    <Router initialPath="/runs/run-1">
      <RunDetail />
    </Router>,
  );

  expect(await screen.findByRole("heading", { name: "NVDA" })).toBeVisible();
  expect(screen.getByText("Run warnings")).toBeVisible();
  expect(
    screen.getByText("One structured output required recovery."),
  ).not.toBeVisible();
  expect(screen.getByRole("tab", { name: "Agent timeline" })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  expect(screen.getByText("1,200")).toBeVisible();

  fireEvent.click(screen.getByRole("tab", { name: "Deliberation" }));
  expect(await screen.findByText("Demand")).toBeVisible();
  expect(screen.getByText("research judge")).toBeVisible();

  fireEvent.click(
    screen.getAllByRole("button", {
      name: "Open evidence ev_0123456789ab",
    })[0],
  );
  expect(
    screen.getByRole("tab", { name: "Evidence" }),
  ).toHaveAttribute("aria-selected", "true");
  expect(
    await screen.findByRole("heading", {
      name: "Price snapshot · Composite snapshot",
    }),
  ).toBeVisible();
  expect(screen.getByText("E01")).toHaveAttribute(
    "title",
    "ev_0123456789ab\nev_fedcba987654",
  );
  expect(
    screen.getByText("1 unique bodies · 2 audit records"),
  ).toBeVisible();
  fireEvent.click(screen.getByText("Canonical IDs and provenance"));
  expect(screen.getByText("fixture-feed", { exact: false })).toBeVisible();
  expect(screen.getByText("ev_fedcba987654")).toBeVisible();
  fireEvent.click(
    screen.getByRole("button", {
      name: "Copy evidence ID ev_fedcba987654",
    }),
  );
  expect(navigator.clipboard.writeText).toHaveBeenCalledWith(
    "ev_fedcba987654",
  );

  fireEvent.click(screen.getByRole("tab", { name: "Reports" }));
  const fundamentalsTab = screen.getByRole("button", {
    name: "Fundamentals",
  });
  const marketTab = screen.getByRole("button", { name: "Market" });
  const newsTab = screen.getByRole("button", { name: "News" });
  const socialTab = screen.getByRole("button", { name: "Sentiment" });
  expect(
    fundamentalsTab.compareDocumentPosition(marketTab) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).not.toBe(0);
  expect(
    marketTab.compareDocumentPosition(newsTab) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).not.toBe(0);
  expect(
    newsTab.compareDocumentPosition(socialTab) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).not.toBe(0);
  expect(
    await screen.findByRole("heading", { name: "Fundamentals report" }),
  ).toBeVisible();
  fireEvent.click(marketTab);
  expect(
    screen.queryByRole("heading", { name: "Data Provenance" }),
  ).not.toBeInTheDocument();
  expect(screen.getByText("Audit details")).toBeVisible();
  expect(
    await screen.findByText("Historical source was partial."),
  ).not.toBeVisible();
  fireEvent.click(screen.getByText("Audit details"));
  expect(screen.getByText("Historical source was partial.")).toBeVisible();
  const inlineRefs = screen.getAllByRole("button", {
    name: /Open evidence ev_/,
  });
  expect(inlineRefs.some((marker) => marker.textContent === "E01")).toBe(true);
  expect(
    screen.queryByText("ev_0123456789ab", { exact: true }),
  ).not.toBeInTheDocument();

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
  const newest = await screen.findByText(/#7/);
  const older = screen.getByText(/#6/);
  expect(
    newest.compareDocumentPosition(older) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).not.toBe(0);
  fireEvent.click(screen.getByRole("button", { name: "Earliest first" }));
  expect(
    older.compareDocumentPosition(newest) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).not.toBe(0);
  expect(localStorage.getItem("tradingagents-timeline-order")).toBe("oldest");
  expect(FakeEventSource.instance.closed).toBe(true);
  await vi.waitFor(() => expect(api.artifacts).toHaveBeenCalledTimes(3));
});

test("opens an editable new-run template instead of rerunning immediately", async () => {
  render(
    <Router initialPath="/runs/run-1">
      <RunDetail />
      <LocationProbe />
    </Router>,
  );

  fireEvent.click(
    await screen.findByRole("link", { name: "New from this run" }),
  );

  expect(screen.getByTestId("router-location")).toHaveTextContent(
    "/runs/new?from_run=run-1",
  );
  expect(api.action).not.toHaveBeenCalled();
});

test("shows trashed retention details and restores without deleting data", async () => {
  vi.mocked(api.run)
    .mockResolvedValueOnce({
      ...detail,
      run: {
        ...detail.run,
        trashed_at: "2026-07-01T00:00:00Z",
      },
    } as RunDetailType)
    .mockResolvedValue({
      ...detail,
      run: { ...detail.run, trashed_at: null },
    } as RunDetailType);
  vi.mocked(api.restoreRuns).mockResolvedValue({
    runs: [],
    changed: 1,
  });

  render(
    <Router initialPath="/runs/run-1">
      <RunDetail />
    </Router>,
  );

  expect(await screen.findByText("NVIDIA Corporation")).toBeVisible();
  expect(screen.getByText("This run is in Trash")).toBeVisible();
  expect(screen.getByText(/Scheduled permanent deletion/)).toBeVisible();
  expect(screen.getByText(/day|due/)).toBeVisible();
  expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Restore" }));
  await waitFor(() =>
    expect(api.restoreRuns).toHaveBeenCalledWith(["run-1"]),
  );
});

test("persists the collapsed audit-details display preference", async () => {
  const first = render(
    <Router initialPath="/runs/run-1?view=reports&report=market">
      <RunDetail />
    </Router>,
  );

  await screen.findByRole("heading", { name: "Market report" });
  expect(screen.getByText("Historical source was partial.")).not.toBeVisible();
  await act(async () => {
    fireEvent.click(screen.getByText("Audit details"));
  });
  expect(localStorage.getItem("tradingagents-audit-details-open")).toBe("true");
  first.unmount();

  render(
    <Router initialPath="/runs/run-1?view=reports&report=market">
      <RunDetail />
    </Router>,
  );

  expect(
    await screen.findByText("Historical source was partial."),
  ).toBeVisible();
});

test("labels runs that have no recorded artifacts", async () => {
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
      "No typed research artifacts were recorded for this run.",
    ),
  ).toBeVisible();
});

test("shows a collapsed per-node metrics table", async () => {
  render(
    <Router initialPath="/runs/run-1">
      <RunDetail />
    </Router>,
  );

  expect(await screen.findByRole("heading", { name: "NVDA" })).toBeVisible();
  const summary = screen.getByText("Per-node metrics", { exact: false });
  const details = summary.closest("details");
  expect(details).not.toHaveAttribute("open");

  fireEvent.click(summary);

  expect(details).toHaveAttribute("open");
  const committee = screen.getByText("committee.final").closest("tr");
  const analyst = screen.getByText("analyst.market").closest("tr");
  expect(committee).toHaveTextContent("400");
  expect(committee).toHaveTextContent("4.5s");
  expect(analyst).toHaveTextContent("800");
  expect(
    committee!.compareDocumentPosition(analyst!) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).not.toBe(0);
});

test("restores report and evidence navigation from the URL", async () => {
  const initialPath = "/runs/run-1?view=reports&report=news";
  const view = render(
    <Router initialPath={initialPath}>
      <RunDetail />
      <LocationProbe />
    </Router>,
  );

  expect(
    await screen.findByRole("heading", { name: "News report" }),
  ).toBeVisible();
  fireEvent.click(
    screen.getAllByRole("button", {
      name: "Open evidence ev_0123456789ab",
    })[0],
  );
  expect(screen.getByTestId("router-location")).toHaveTextContent(
    "/runs/run-1?view=evidence&ref=ev_0123456789ab&return_view=reports&return_report=news",
  );

  fireEvent.click(
    screen.getByRole("button", { name: /Return to reports/ }),
  );
  expect(screen.getByTestId("router-location")).toHaveTextContent(
    "/runs/run-1?view=reports&report=news",
  );
  expect(
    await screen.findByRole("heading", { name: "News report" }),
  ).toBeVisible();

  const restoredPath =
    screen.getByTestId("router-location").textContent ?? initialPath;
  view.unmount();
  render(
    <Router initialPath={restoredPath}>
      <RunDetail />
    </Router>,
  );

  expect(
    await screen.findByRole("heading", { name: "News report" }),
  ).toBeVisible();
});

test("localizes canonical report labels for zh-CN", async () => {
  await i18n.changeLanguage("zh-CN");
  render(
    <Router initialPath="/runs/run-1?view=reports">
      <RunDetail />
    </Router>,
  );

  expect(
    await screen.findByRole("heading", { name: "Fundamentals report" }),
  ).toBeVisible();
  const labels = ["基本面", "市场", "新闻", "舆情"].map((name) =>
    screen.getByRole("button", { name }),
  );
  labels.slice(0, -1).forEach((label, index) => {
    expect(
      label.compareDocumentPosition(labels[index + 1]) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).not.toBe(0);
  });
});
