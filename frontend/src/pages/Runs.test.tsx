import {
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import {
  api,
  type Capabilities,
  type RunPage,
  type RunSummaryView,
} from "../api/client";
import i18n from "../i18n";
import { Router, useLocation } from "../router";
import Runs from "./Runs";

vi.mock("../api/client", () => ({
  api: {
    runs: vi.fn(),
    capabilities: vi.fn(),
    trashRuns: vi.fn(),
    restoreRuns: vi.fn(),
  },
}));

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
      asset_type: "stock",
      profile: "standard",
      analysts: ["market"],
      llm_provider: "openai",
      quick_model: "quick",
      deep_model: "deep",
      quick_reasoning_effort: "provider_default",
      deep_reasoning_effort: "provider_default",
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

const capabilities = {
  defaults: { trash_retention_days: 30 },
} as Capabilities;

function page(items: RunSummaryView[], offset = 0, total = items.length): RunPage {
  return { items, limit: 20, offset, total };
}

function LocationProbe() {
  const location = useLocation();
  return (
    <output data-testid="location">
      {location.pathname}
      {location.search}
    </output>
  );
}

beforeEach(async () => {
  vi.resetAllMocks();
  await i18n.changeLanguage("en");
  vi.mocked(api.capabilities).mockResolvedValue(capabilities);
  vi.mocked(api.trashRuns).mockResolvedValue({
    runs: [],
    changed: 1,
  });
  vi.mocked(api.restoreRuns).mockResolvedValue({
    runs: [],
    changed: 1,
  });
});

test("filters and atomically trashes eligible runs with instrument names", async () => {
  const incremental = run("run-1", "NVDA", "succeeded");
  incremental.research_kind = "incremental";
  incremental.research_confidence = 0.82;
  vi.mocked(api.runs).mockResolvedValue(
    page([
      incremental,
      run("run-2", "AAPL", "running"),
    ]),
  );
  render(
    <Router initialPath="/runs">
      <Runs />
      <LocationProbe />
    </Router>,
  );

  expect(await screen.findByText("NVIDIA Corporation")).toBeVisible();
  expect(screen.getByText("英伟达")).toBeVisible();
  expect(screen.getByText("Overweight")).toHaveClass("research-rating-badge");
  expect(screen.getByText("82% confidence")).toBeVisible();
  const kindBadge = screen.getByRole("button", { name: "Incremental research" });
  expect(kindBadge).toHaveClass("research-kind-badge");
  fireEvent.click(kindBadge);
  const configuration = screen.getByRole("tooltip", {
    name: "Research configuration",
  });
  expect(configuration).toHaveTextContent("1 information domain");
  expect(configuration).toHaveTextContent("Market");
  expect(configuration).toHaveTextContent("openai");
  expect(configuration).toHaveTextContent("deep");
  expect(configuration).toHaveTextContent("Provider default");
  expect(configuration).not.toHaveTextContent("Quick model");
  expect(screen.queryByRole("button", { name: "Configuration" })).not.toBeInTheDocument();
  expect(screen.getByText("—")).toHaveClass("research-rating-badge");
  expect(screen.getByLabelText("Select run AAPL")).toBeDisabled();
  fireEvent.click(screen.getByLabelText("Select run NVDA"));
  fireEvent.click(
    screen.getByRole("button", { name: "Move to Trash (1)" }),
  );

  const dialog = screen.getByRole("alertdialog", {
    name: "Move 1 selected run(s) to Trash?",
  });
  expect(dialog).toHaveTextContent(
    "They will immediately leave the Dashboard and instrument suggestions.",
  );
  expect(dialog).toHaveTextContent(/scheduled for permanent deletion/);
  expect(api.trashRuns).not.toHaveBeenCalled();
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  fireEvent.click(
    screen.getByRole("button", { name: "Move to Trash (1)" }),
  );
  fireEvent.click(
    screen.getByRole("button", { name: /^Move to Trash$/ }),
  );

  await waitFor(() =>
    expect(api.trashRuns).toHaveBeenCalledWith(["run-1"]),
  );

  fireEvent.change(screen.getByLabelText("Search runs"), {
    target: { value: "nvidia" },
  });
  fireEvent.change(screen.getByLabelText("Status"), {
    target: { value: "succeeded" },
  });
  fireEvent.change(screen.getByLabelText("Research kind"), {
    target: { value: "incremental" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));

  await waitFor(() =>
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/runs?q=nvidia&status=succeeded&research_kind=incremental",
    ),
  );
  await waitFor(() => {
    const lastQuery = vi.mocked(api.runs).mock.calls.at(-1)?.[0] ?? "";
    const params = new URLSearchParams(lastQuery.replace(/^\?/, ""));
    expect(Object.fromEntries(params)).toMatchObject({
      trash_state: "active",
      q: "nvidia",
      status: "succeeded",
      research_kind: "incremental",
      limit: "20",
      offset: "0",
    });
  });
});

test("restores trashed runs and returns from an emptied page", async () => {
  const trashed = run(
    "run-trashed",
    "NVDA",
    "failed",
    "2026-07-01T00:00:00Z",
  );
  trashed.research_schema_version = "1";
  trashed.is_research_node = false;
  vi.mocked(api.runs).mockResolvedValue(page([trashed], 20, 41));

  render(
    <Router initialPath="/runs?trash_state=trashed&offset=20">
      <Runs />
      <LocationProbe />
    </Router>,
  );

  await screen.findByText("NVIDIA Corporation");
  expect(screen.getByText("Trash retention")).toBeVisible();
  expect(
    screen.getByText(/Runs are permanently deleted 30 days/),
  ).toBeVisible();
  expect(screen.getByText("July 31, 2026")).toBeVisible();
  fireEvent.click(screen.getByLabelText("Select run NVDA"));
  fireEvent.click(
    screen.getByRole("button", { name: "Restore selected (1)" }),
  );
  await waitFor(() =>
    expect(api.restoreRuns).toHaveBeenCalledWith(["run-trashed"]),
  );
  await waitFor(() =>
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/runs?trash_state=trashed&offset=0",
    ),
  );
});

test("keeps a single Open action for Research Nodes", async () => {
  const node = run("full-node", "NVDA", "succeeded");
  node.research_schema_version = "1";
  node.is_research_node = true;
  vi.mocked(api.runs).mockResolvedValue(page([node]));

  render(
    <Router initialPath="/runs">
      <Runs />
    </Router>,
  );

  expect(await screen.findByLabelText("Select run NVDA")).toBeDisabled();
  expect(screen.getByRole("columnheader", { name: "Actions" })).toBeVisible();
  expect(screen.queryByRole("link", { name: "Research Timeline" })).toBeNull();
  const open = screen.getByRole("link", { name: "Open" });
  expect(open).toHaveAttribute("href", "/runs/full-node");
  expect(open).toHaveClass("compact-button");
});
