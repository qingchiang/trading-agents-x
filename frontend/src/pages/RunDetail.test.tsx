import {
  act,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import {
  api,
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
    summary: `${title} summary`,
    claims: [],
    confidence: 0.7,
    evidence_refs: ["ev_0123456789ab"],
    warnings,
    narrative: `# ${title} report\n\nEvidence-grounded narrative.`,
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
        narrative:
          "# Market report\n\nCompare ev_0123456789ab with ev_fedcba987654.",
      },
      fundamentals: analystReport("fundamentals", "Fundamentals"),
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
      node_wall_times: {
        "analyst.market": 2.1,
        "legacy.node": 1.2,
      },
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
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
  });
  localStorage.removeItem("tradingagents-timeline-order");
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
    await screen.findByText("Historical source was partial."),
  ).toBeVisible();
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

test("shows a collapsed per-node metrics table with legacy fallback", async () => {
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
  const legacy = screen.getByText("legacy.node").closest("tr");
  expect(committee).toHaveTextContent("400");
  expect(committee).toHaveTextContent("4.5s");
  expect(analyst).toHaveTextContent("800");
  expect(legacy).toHaveTextContent("—");
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
    screen.getByRole("button", {
      name: "Open evidence ev_0123456789ab",
    }),
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

test("diagnoses 7011.T-style legacy JSON, markdown, and rating conflicts", async () => {
  const nestedDecision = {
    rating: "Overweight",
    confidence: 0.4,
    thesis: "Parsed final thesis is constructive but uncertain.",
    evidence_refs: ["ev_0123456789ab"],
    memory_refs: [],
    catalysts: ["Order growth"],
    risks: ["Valuation compression"],
    invalidation_conditions: ["Guidance misses"],
    time_horizon: "6-12 months",
  };
  const degradedDetail = structuredClone(detail) as RunDetailType;
  if (!degradedDetail.result) throw new Error("fixture result missing");
  degradedDetail.result.decision = {
    rating: "Hold",
    confidence: 0.3,
    thesis: JSON.stringify(nestedDecision),
    evidence_refs: [
      "ev_0123456789ab",
      "ev_fedcba987654",
    ],
    memory_refs: [],
    catalysts: [],
    risks: ["Structured decision output was unavailable."],
    invalidation_conditions: [
      "Reassess when higher-quality evidence becomes available.",
    ],
    time_horizon: "Unspecified research horizon",
  };
  const degradedArtifacts = [
    {
      id: "artifact-bear",
      run_id: "run-1",
      attempt: 1,
      stage: "perspective",
      role: "bear",
      round: 0,
      schema_version: "1",
      generation_method: "legacy_unknown",
      diagnostics: {
        degraded_output: true,
        legacy_degraded_output: true,
        reason_codes: [
          "nested_json_thesis",
          "missing_structured_fields",
        ],
        missing_fields: ["claim_rebuttals", "risks"],
        sentinel_fields: [],
        parsed_thesis: {
          summary: "Legacy bear summary.",
          challenged_claims: [
            {
              claim_text: "Guidance is reliable.",
              challenge_text: "Guidance remains untested.",
              evidence_refs: ["ev_0123456789ab"],
            },
          ],
          downside_mechanisms: [
            {
              mechanism: "Multiple compression.",
              detail: "Rates remain restrictive.",
              evidence_refs: ["ev_fedcba987654"],
            },
          ],
          evidence_refs: ["ev_0123456789ab"],
        },
        outer_rating: null,
        nested_rating: null,
        rating_conflict: false,
        rerun_recommended: true,
      },
      created_at: "2026-07-24T00:00:40Z",
      content: {
        role: "bear",
        thesis: JSON.stringify({
          summary: "Legacy bear summary.",
          challenged_claims: ["Guidance remains untested."],
          downside_mechanisms: ["Multiple compression."],
        }),
        claim_rebuttals: [],
        evidence_refs: ["ev_0123456789ab"],
        new_evidence_refs: [],
        risks: [],
      },
    },
    {
      id: "artifact-risk",
      run_id: "run-1",
      attempt: 1,
      stage: "risk",
      role: "risk",
      round: 0,
      schema_version: "1",
      generation_method: "legacy_unknown",
      diagnostics: {
        degraded_output: true,
        legacy_degraded_output: true,
        reason_codes: ["missing_structured_fields"],
        missing_fields: ["claim_rebuttals", "risks"],
        sentinel_fields: [],
        parsed_thesis: null,
        outer_rating: null,
        nested_rating: null,
        rating_conflict: false,
        rerun_recommended: true,
      },
      created_at: "2026-07-24T00:00:45Z",
      content: {
        role: "risk",
        thesis: "## Risk reviewer markdown\n\nVisible legacy narrative.",
        claim_rebuttals: [],
        evidence_refs: ["ev_0123456789ab"],
        new_evidence_refs: [],
        risks: [],
      },
    },
    {
      id: "artifact-final",
      run_id: "run-1",
      attempt: 1,
      stage: "decision",
      role: "final_committee",
      round: 0,
      schema_version: "1",
      generation_method: "legacy_unknown",
      diagnostics: {
        degraded_output: true,
        legacy_degraded_output: true,
        reason_codes: [
          "nested_json_thesis",
          "fallback_sentinel_fields",
          "rating_conflict",
        ],
        missing_fields: [],
        sentinel_fields: [
          "risks",
          "invalidation_conditions",
          "time_horizon",
        ],
        parsed_thesis: nestedDecision,
        outer_rating: "Hold",
        nested_rating: "Overweight",
        rating_conflict: true,
        rerun_recommended: true,
      },
      created_at: "2026-07-24T00:00:50Z",
      content: degradedDetail.result.decision,
    },
  ] as unknown as ResearchArtifact[];
  vi.mocked(api.run).mockResolvedValue(degradedDetail);
  vi.mocked(api.artifacts).mockResolvedValue(degradedArtifacts);

  render(
    <Router initialPath="/runs/run-1?view=deliberation">
      <RunDetail />
    </Router>,
  );

  expect(await screen.findByText("Legacy bear summary.")).toBeVisible();
  expect(screen.getByText("Guidance remains untested.")).toBeVisible();
  expect(screen.getByText("Multiple compression.")).toBeVisible();
  expect(screen.getByText("Rates remain restrictive.")).toBeVisible();
  expect(
    screen.getByRole("heading", { name: "Risk reviewer markdown" }),
  ).toBeVisible();
  expect(
    screen.getAllByText(
      "Legacy degraded output did not capture this field.",
    ),
  ).toHaveLength(2);

  fireEvent.click(screen.getByRole("tab", { name: "Decision" }));
  expect(await screen.findByText("Conflicting research ratings")).toBeVisible();
  expect(
    screen.getByText(/Stored outer rating: Hold.*Nested payload rating: Overweight/),
  ).toBeVisible();
  expect(
    screen.getByText("Parsed final thesis is constructive but uncertain."),
  ).toBeVisible();
  expect(screen.getByText("Order growth")).toBeVisible();
  expect(
    screen.getByText(
      "Do not treat this artifact as a reliable conclusion. Rerun the research.",
    ),
  ).toBeVisible();
  expect(screen.getByText("Canonical degraded payload")).toBeVisible();
});
