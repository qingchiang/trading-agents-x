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
  expect(FakeEventSource.instance.url).toBe("/api/v1/runs/run-1/events?after=11&event_format=message");
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



test("diagnostics receives arbitrary replayed and live events without an event-type whitelist", async () => {
  render(<Router initialPath="/runs/run-1?view=diagnostics"><ReaderFixture /></Router>);
  await screen.findByRole("heading", { name: "Execution events" });
  const stream = FakeEventSource.instance;
  const emit = (sequence: number, event_type: string) => stream.emit("message", {
    run_id: "run-1", sequence, attempt: 1, event_type, node: "decision",
    payload: { field_path: "market_reference_levels.0", validation_issues: ["reference.refs_invalid"] },
    created_at: "2026-08-27T09:59:17Z",
  });
  await act(async () => { emit(1, "node.numeric_audit_degraded"); emit(2, "decision.reference_omitted"); });
  expect(screen.getByRole("option", { name: "node.numeric_audit_degraded" })).toBeVisible();
  expect(screen.getByRole("option", { name: "decision.reference_omitted" })).toBeVisible();
  await act(async () => { emit(3, "future.diagnostic"); emit(3, "future.diagnostic"); });
  expect(screen.getByRole("option", { name: "future.diagnostic" })).toBeVisible();
  expect(document.querySelectorAll(".diagnostic-event")).toHaveLength(3);
  fireEvent.click(screen.getByRole("button", { name: "Raw record: 2 · decision.reference_omitted" }));
  expect((await screen.findAllByText(/reference.refs_invalid/)).length).toBeGreaterThan(0);
});
