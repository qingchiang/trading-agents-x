import {
act,
fireEvent,
render,
screen,
waitFor,
within,
} from "@testing-library/react";
import { expect,test,vi } from "vitest";
import { Router } from "../../app/router";
import {
api,
type ResearchNodeView,
type RunDetail as RunDetailType,
type RunEvent
} from "../../shared/api/client";
import { detail,FakeEventSource,ReaderFixture } from "../research/researchTestFixtures";

test("shows decision audit gaps without an empty candidate viewer", async () => {
  const degraded = structuredClone(detail);
  degraded.result!.decision!.numeric_audit_status = "partial";
  degraded.result!.numeric_audit = {
    status: "partial",
    omitted_components: [
      {
        component_path: "thesis",
        component_type: "decision_claim",
        reference_label: "Forward PE",
        issue_codes: [
          "numeric.requirement_candidate.0.inputs.list_type",
          "numeric.requirement_candidate.0.limitations.missing",
        ],
      },
    ],
    snapshots: [],
  };
  vi.mocked(api.run).mockResolvedValue(degraded);

  render(
    <Router initialPath="/runs/run-1?view=diagnostics">
      <ReaderFixture />
    </Router>,
  );

  const summary = await screen.findByText(
    "Decision-critical calculation audit",
  );
  fireEvent.click(summary);
  expect(screen.getByText("Decision claim · Forward PE")).toBeVisible();
  expect(
    screen.getByText("numeric.requirement_candidate.0.inputs.list_type"),
  ).toBeVisible();
  expect(
    screen.getByText("numeric.requirement_candidate.0.limitations.missing"),
  ).toBeVisible();
  expect(
    screen.getByText(
      "These decision-critical derived values were not fully verified. The qualitative decision is retained, but the listed values are excluded from canonical calculations.",
    ),
  ).toBeVisible();
  expect(
    screen.queryByText("The provider output could not be parsed as a JSON object."),
  ).not.toBeInTheDocument();
});

test("shows requirement comparisons separately from candidate drafts", async () => {
  const compared = structuredClone(detail);
  compared.result!.decision!.numeric_audit_status = "partial";
  compared.result!.numeric_audit = {
    status: "partial",
    requirement_checks: [
      {
        requirement_id: "req_forward_pe",
        calculation_id: "calc_forward_pe",
        component_path: "thesis",
        label: "Forward PE",
        stated_value: 45.8,
        fraction_digits: 1,
        unit: "x",
        formula: "price / eps",
        inputs: { price: 3834.343755, eps: 1 },
        input_evidence_refs: ["ev_0123456789ab"],
        canonical_result: 3834.343755,
        rounded_stated_value: 45.8,
        rounded_canonical_result: 3834.3,
        calculation_status: "verified",
        display_status: "mismatched",
        issue_codes: ["numeric.requirement.req_forward_pe.result_mismatch"],
      },
    ],
    omitted_components: [],
    snapshots: [],
  };
  vi.mocked(api.run).mockResolvedValue(compared);

  render(
    <Router initialPath="/runs/run-1?view=diagnostics">
      <ReaderFixture />
    </Router>,
  );

  const summary = await screen.findByText("Decision-critical calculation audit");
  expect(summary).toBeVisible();
  expect(screen.getAllByText("Calculation verified")[0]).toBeVisible();
  expect(screen.getByText("Display mismatched")).toBeVisible();
  expect(
    screen.getByText(
      "The calculation is valid, but the decision text or display scale does not match.",
    ),
  ).not.toBeVisible();
  expect(screen.queryByText("price / eps")).not.toBeVisible();
  fireEvent.click(within(screen.getByText("Forward PE", { selector: "strong" }).closest("article")!).getByText("Formula and Evidence"));
  expect(screen.getByText("price / eps")).toBeVisible();
  expect(screen.getByText("numeric.requirement.req_forward_pe.result_mismatch")).toBeVisible();
});

test("formats recorded calculations without inventing a per-item audit", async () => {
  render(
    <Router initialPath="/runs/run-1?view=diagnostics">
      <ReaderFixture />
    </Router>,
  );

  await screen.findByRole("heading", { name: "Decision-critical calculation audit" });
  const calculation = screen.getAllByText("Observed market anchor")[0].closest("article");
  expect(calculation).not.toBeNull();
  expect(within(calculation!).getByText("Per-item audit not recorded")).toBeVisible();
  expect(within(calculation!).getByText("100 USD", { selector: "strong" })).toBeVisible();
  expect(within(calculation!).getByText("Jul 24, 2026")).not.toBeVisible();
  expect(within(calculation!).getByText("calc_market_reference")).not.toBeVisible();
  fireEvent.click(within(calculation!).getByText("Formula and Evidence"));
  expect(within(calculation!).getByText("calc_market_reference")).toBeVisible();
  expect(within(calculation!).getByRole('button', { name: 'Raw record: Inputs' })).toBeVisible();
});

test("streams through historical failed attempts before closing on the current success", async () => {
  const retried = structuredClone(detail) as RunDetailType;
  retried.run.attempt = 4;
  retried.run.status = "succeeded";
  vi.mocked(api.run).mockResolvedValue(retried);

  render(
    <Router initialPath="/runs/run-1?view=timeline">
      <ReaderFixture />
    </Router>,
  );
  await screen.findByRole("heading", { name: "Run progress" });
  const stream = FakeEventSource.instance;

  await act(async () => {
    stream.emit("run.failed", {
      run_id: "run-1",
      sequence: 11,
      attempt: 1,
      event_type: "run.failed",
      node: null,
      payload: {},
      created_at: "2026-08-27T09:59:17Z",
    });
  });
  expect(stream.closed).toBe(false);

  await act(async () => {
    stream.emit("run.succeeded", {
      run_id: "run-1",
      sequence: 47,
      attempt: 4,
      event_type: "run.succeeded",
      node: null,
      payload: {},
      created_at: "2026-08-27T15:08:50Z",
    });
  });
  expect(stream.closed).toBe(true);
});

test("opens a fresh event stream from the last sequence after retry", async () => {
  const failed = structuredClone(detail) as RunDetailType;
  failed.run.status = "failed";
  failed.run.attempt = 1;
  failed.result!.status = "failed";
  const queued = structuredClone(failed) as RunDetailType;
  queued.run.status = "queued";
  queued.run.attempt = 2;
  queued.result!.status = "queued";
  let current = failed;
  vi.mocked(api.run).mockImplementation(async () => current);
  vi.mocked(api.action).mockImplementation(async () => {
    current = queued;
    return queued.run;
  });

  render(
    <Router initialPath="/runs/run-1?view=timeline">
      <ReaderFixture />
    </Router>,
  );
  await screen.findByRole("heading", { name: "Run progress" });
  const first = FakeEventSource.instance;
  act(() => first.emit("run.failed", {
    run_id: "run-1",
    sequence: 11,
    attempt: 1,
    event_type: "run.failed",
    node: null,
    payload: {},
    created_at: "2026-08-27T09:59:17Z",
  }));
  expect(first.closed).toBe(true);

  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() => expect(FakeEventSource.instances).toHaveLength(2));
  expect(FakeEventSource.instance.url).toBe("/api/v1/runs/run-1/events?after=11");
});

test("localizes Incremental activity and keeps one technical event log per attempt", async () => {
  const incremental = structuredClone(detail) as RunDetailType;
  incremental.run.research_kind = "incremental";
  incremental.run.full_baseline_run_id = "full-baseline";
  incremental.run.is_research_node = true;
  incremental.research_node = {
    id: "run-1",
    cycle_id: "full-baseline",
    instrument: "NVDA",
    analysis_date: "2026-07-24",
    research_kind: "incremental",
    full_baseline_run_id: "full-baseline",
    research_schema_version: "1",
    information_cutoff_at: "2026-07-24T20:00:00Z",
    method_snapshot: {},
    is_baseline_compatible: false,
    is_active: true,
    is_primary: true,
    is_cycle_head: true,
    cycle_warning: false,
    full_research_required_reasons: [],
  } as ResearchNodeView;
  vi.mocked(api.run).mockResolvedValue(incremental);

  render(
    <Router initialPath="/runs/run-1?view=timeline">
      <ReaderFixture />
    </Router>,
  );
  await screen.findByRole("heading", { name: "Run progress" });

  act(() =>
    FakeEventSource.instance.emit("incremental.collection_completed", {
      run_id: "run-1",
      sequence: 9,
      attempt: 1,
      event_type: "incremental.collection_completed",
      node: "incremental.collect",
      payload: { domains: 4 },
      created_at: "2026-07-24T00:01:20Z",
    } as RunEvent),
  );
  act(() =>
    FakeEventSource.instance.emit("node.output_retry", {
      run_id: "run-1",
      sequence: 10,
      attempt: 1,
      event_type: "node.output_retry",
      node: "incremental.synthesis.serialize",
      payload: { reason_code: "schema_validation" },
      created_at: "2026-07-24T00:01:21Z",
    } as RunEvent),
  );

  const activityTitle = await screen.findByText("Collection · Collection update");
  const workUnit = activityTitle.closest("article");
  expect(within(workUnit!).getByText("Completed")).toBeVisible();
  expect(within(workUnit!).queryByText("incremental.collect")).toBeNull();
  expect(screen.queryByText("schema_validation")).toBeNull();
  const attempt = screen.getByText("Attempt 1").closest("details");
  expect(within(attempt!).queryByText("Audit details")).not.toBeInTheDocument();
  expect(within(attempt!).queryByText("Technical events (2)")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("link", { name: "Diagnostics" }));
  await screen.findByRole("heading", { name: "Execution events" });
  expect(screen.getByText("incremental.collection_completed", { selector: "strong" })).toBeVisible();

});

test("orders work units within each attempt and restores the activity preference", async () => {
  localStorage.setItem("tradingagents-timeline-order", "oldest");
  render(
    <Router initialPath="/runs/run-1?view=timeline">
      <ReaderFixture />
    </Router>,
  );
  await screen.findByRole("heading", { name: "Run progress" });

  act(() => {
    FakeEventSource.instance.emit("node.completed", {
      run_id: "run-1",
      sequence: 9,
      attempt: 1,
      event_type: "node.completed",
      node: "analyst.news.report",
      payload: {},
      created_at: "2026-07-24T00:01:20Z",
    } as RunEvent);
    FakeEventSource.instance.emit("node.completed", {
      run_id: "run-1",
      sequence: 10,
      attempt: 1,
      event_type: "node.completed",
      node: "risk.review",
      payload: {},
      created_at: "2026-07-24T00:01:21Z",
    } as RunEvent);
  });

  const attempt = screen.getByText("Attempt 1").closest("details");
  expect(attempt).not.toBeNull();
  const nodes = () =>
    Array.from(attempt!.querySelectorAll(".activity-work-unit strong")).map(element => element.textContent);

  expect(screen.getByRole("button", { name: "Earliest first" })).toHaveAttribute(
    "aria-pressed",
    "true",
  );
  expect(nodes()[0]).toMatch(/Analysis reports/);
  expect(nodes()[1]).toMatch(/Risk/);

  fireEvent.click(screen.getByRole("button", { name: "Latest first" }));
  expect(nodes()[0]).toMatch(/Risk/);
  expect(nodes()[1]).toMatch(/Analysis reports/);
  expect(localStorage.getItem("tradingagents-timeline-order")).toBe("newest");
});

test("shows persisted run metrics when a failed run has no result", async () => {
  vi.mocked(api.run).mockResolvedValue({
    ...detail,
    run: {
      ...detail.run,
      status: "failed",
      error_code: "StructuredOutputError",
      error_message: "Validated output failed.",
    },
    result: null,
    attempts: [
      {
        ...detail.attempts![0],
        status: "failed",
        error_code: "StructuredOutputError",
      },
    ],
  } as RunDetailType);

  render(
    <Router initialPath="/runs/run-1?view=diagnostics">
      <ReaderFixture />
    </Router>,
  );

  expect(await screen.findByText("Validated output failed.")).toBeVisible();
  expect(screen.getAllByText("1,200")[0]).toBeVisible();
  fireEvent.click(screen.getByText("Attempt metrics", { exact: false }));
  expect(screen.getByText("StructuredOutputError")).toBeVisible();
});

test('retries unavailable baseline audit data and opens inherited calculation evidence in diagnostics', async () => {
  const incremental = structuredClone(detail) as RunDetailType;
  incremental.run.id = 'increment-audit';
  incremental.run.research_kind = 'incremental';
  incremental.run.full_baseline_run_id = 'baseline-audit';
  incremental.result!.numeric_audit = null;
  const baseline = structuredClone(detail) as RunDetailType;
  baseline.run.id = 'baseline-audit';
  baseline.run.research_kind = 'full';
  incremental.result!.evidence!.items = [];
  let available = false;
  vi.mocked(api.run).mockImplementation(async id => {
    if (id === 'baseline-audit') {
      if (!available) throw new Error('Baseline temporarily unavailable');
      return baseline;
    }
    return incremental;
  });
  render(<Router initialPath="/runs/increment-audit?view=diagnostics"><ReaderFixture /></Router>);
  expect(await screen.findByText('Full baseline audit and evidence could not be loaded. Current records remain available.')).toBeVisible();
  available = true;
  fireEvent.click(screen.getByRole('button', { name: 'Try again' }));
  await screen.findAllByText('Calculation record unchanged from full baseline');
  const row = document.querySelector('.numeric-audit-appendix article')!;
  fireEvent.click(within(row as HTMLElement).getByText('Formula and Evidence'));
  const source = within(row as HTMLElement).getByRole('button', { name: /Open evidence/ });
  source.focus();
  fireEvent.click(source);
  expect(await screen.findByRole('dialog')).toHaveTextContent('Full baseline');
  expect(screen.getByRole('dialog')).toHaveTextContent('Close: 100 USD');
  fireEvent.click(screen.getByRole('button', { name: 'Close' }));
  expect(source).toHaveFocus();
});

vi.mock("../../shared/api/client", () => ({
  api: {
    run: vi.fn(),
    analysisCutoffContext: vi.fn(),
    evidence: vi.fn(),
    artifacts: vi.fn(),
    action: vi.fn(),
    capabilities: vi.fn(),
    restoreRuns: vi.fn(),
  },
}));

