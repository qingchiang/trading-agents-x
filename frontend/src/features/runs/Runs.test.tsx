import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import { api, type RunGroupPage, type RunSummaryView } from "../../shared/api/client";
import i18n from "../../shared/i18n";
import { Router } from "../../app/router";
import Runs from "./Runs";
vi.mock("../../shared/api/client", () => ({ api: { runGroups: vi.fn(), capabilities: vi.fn(), previewLifecycle: vi.fn(), trashRuns: vi.fn(), restoreRuns: vi.fn(), purgeRuns: vi.fn(), action: vi.fn() } }));
function run(
  id: string,
  ticker: string,
  status: RunSummaryView["status"],
  trashedAt: string | null = null,
): RunSummaryView {
  return {
    id,
    source_run_id: null,
    instrument_name:
      ticker === "NVDA" ? "NVIDIA Corporation" : "Apple Inc.",
    instrument_local_name: ticker === "NVDA" ? "英伟达" : null,
    research_rating: status === "succeeded" ? "Overweight" : null,
    trashed_at: trashedAt,
    status,
    request: {
      ticker,
      analysis_date: "2026-07-24",

      profile: "standard",
      analysts: ["market"],
      models: { quick: { connection_id: "openai", model: "quick", reasoning_effort: "provider_default" }, deep: { connection_id: "openai", model: "deep", reasoning_effort: "provider_default" } },
      output_language: "en",
    },
    config_snapshot: {},
    attempt: 1,
    cancel_requested: false,
    error_code: null,
    error_message: null,
    metrics: {
      llm_calls: 0,
      tool_calls: 0,
      input_tokens: 0,
      output_tokens: 0,
      wall_time_seconds: 0,
      node_metrics: {},
    },
    created_at: "2026-07-24T00:00:00Z",
    started_at: null,
    finished_at: "2026-07-24T00:01:00Z",
    updated_at: "2026-07-24T00:01:00Z",
  };
}

function groups(items: RunSummaryView[], offset = 0): RunGroupPage {
  return { items: items.map(item => ({ id: item.id, instrument: item.request.ticker, kind: "standalone", related_tasks: [item], research_runs: [], matched_run_ids: [item.id], status_counts: { [item.status]: 1 } })), total: offset + items.length, limit: 12, offset };
}
beforeEach(async () => {
  vi.resetAllMocks(); await i18n.changeLanguage("en");
  vi.mocked(api.capabilities).mockResolvedValue({ defaults: { trash_retention_days: 30 } } as never);
  vi.mocked(api.trashRuns).mockResolvedValue({ runs: [], changed: 1 });
  vi.mocked(api.restoreRuns).mockResolvedValue({ runs: [], changed: 1 });
  vi.mocked(api.previewLifecycle).mockImplementation(async (ids, action) => ({ action, affected_run_ids: ids, affected_runs: ids.map(id => run(id, "NVDA", "succeeded")), blocked_reasons: [], primary_replacements: {} }));
});
test("previews terminal task deletion and filters groups on the server", async () => {
  vi.mocked(api.runGroups).mockResolvedValue(groups([run("one", "NVDA", "succeeded"), run("two", "AAPL", "running")]));
  render(<Router initialPath="/runs"><Runs /></Router>);
  await screen.findByText("NVIDIA Corporation");
  expect(screen.getByLabelText("Select run AAPL")).toBeDisabled();
  fireEvent.click(screen.getByLabelText("Select run NVDA"));
  fireEvent.click(screen.getByRole("button", { name: "Move to Trash (1)" }));
  const dialog = await screen.findByRole("alertdialog");
  expect(await within(dialog).findByText("Affected records: 1")).toBeVisible();
  expect(api.trashRuns).not.toHaveBeenCalled();
  fireEvent.click(within(dialog).getByRole("button", { name: "Confirm Trash" }));
  await waitFor(() => expect(api.trashRuns).toHaveBeenCalledWith(["one"], {}, ["one"]));
  await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
  fireEvent.change(screen.getByLabelText("Search runs"), { target: { value: "nvidia" } });
  fireEvent.change(screen.getByLabelText("Status"), { target: { value: "failed" } });
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  await waitFor(() => expect(Object.fromEntries(new URLSearchParams(vi.mocked(api.runGroups).mock.calls.at(-1)![0]))).toMatchObject({ q: "nvidia", status: "failed" }));
});
test("restores a trashed task after preview and resets an emptied page", async () => {
  vi.mocked(api.runGroups).mockResolvedValue(groups([run("one", "NVDA", "failed", "2026-07-01T00:00:00Z")], 12));
  render(<Router initialPath="/runs?trash_state=trashed&offset=12"><Runs /></Router>);
  await screen.findByText("NVIDIA Corporation");
  expect(screen.getByText("July 31, 2026")).toBeVisible();
  fireEvent.click(screen.getByLabelText("Select run NVDA"));
  fireEvent.click(screen.getByRole("button", { name: "Restore selected (1)" }));
  const dialog = await screen.findByRole("alertdialog");
  await within(dialog).findByText("Affected records: 1");
  fireEvent.click(within(dialog).getByRole("button", { name: "Restore" }));
  await waitFor(() => expect(api.restoreRuns).toHaveBeenCalledWith(["one"], ["one"]));
  await waitFor(() => expect(api.runGroups).toHaveBeenLastCalledWith(expect.stringContaining("offset=0")));
});
test("shows a baseline, its research and related unfinished tasks as separate ownership", async () => {
  const baseline = { ...run("full", "NVDA", "succeeded"), is_research_node: true, research_kind: "full" as const };
  const child = { ...run("child", "NVDA", "running"), research_kind: "incremental" as const, full_baseline_run_id: "full" };
  vi.mocked(api.runGroups).mockResolvedValue({ limit: 12, offset: 0, total: 1, items: [{ id: "full", kind: "cycle", instrument: "NVDA", baseline, is_primary: true, research_runs: [baseline], related_tasks: [child], matched_run_ids: [child.id] }] });
  render(<Router initialPath="/runs?status=running"><Runs /></Router>);
  await screen.findByText("NVIDIA Corporation");
  expect(screen.getByText("Primary Cycle")).toBeVisible();
  expect(screen.getByText(/Related tasks — not yet committed/)).toBeVisible();
  expect(screen.getByRole("link", { name: "Read research" })).toHaveAttribute("href", "/timelines/NVDA?node=full");
  expect(screen.getByLabelText("Select run NVDA")).toBeDisabled();
  expect(screen.getAllByRole("link", { name: "Execution details" })).toHaveLength(2);
});

test("refreshes task state without clearing an unsubmitted search", async () => {
  vi.mocked(api.runGroups).mockResolvedValue(groups([run("one", "NVDA", "running")]));
  render(<Router initialPath="/runs"><Runs /></Router>);
  await screen.findByText("NVIDIA Corporation");
  fireEvent.change(screen.getByLabelText("Search runs"), { target: { value: "draft search" } });
  vi.useFakeTimers();
  try {
    await act(async () => { fireEvent(window, new Event("focus")); });
    expect(api.runGroups).toHaveBeenCalledTimes(2);
    expect(screen.getByLabelText("Search runs")).toHaveValue("draft search");
  } finally { vi.useRealTimers(); }
});
