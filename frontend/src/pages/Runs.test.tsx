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
  type RunView,
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
  status: RunView["status"],
  trashedAt: string | null = null,
): RunView {
  return {
    id,
    source_run_id: null,
    instrument_name:
      ticker === "NVDA" ? "NVIDIA Corporation" : "Apple Inc.",
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

function page(items: RunView[], offset = 0, total = items.length): RunPage {
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
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

test("filters and atomically trashes eligible runs with instrument names", async () => {
  vi.mocked(api.runs).mockResolvedValue(
    page([
      run("run-1", "NVDA", "succeeded"),
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
  expect(screen.getByLabelText("Select run AAPL")).toBeDisabled();
  fireEvent.click(screen.getByLabelText("Select run NVDA"));
  fireEvent.click(
    screen.getByRole("button", { name: "Move to Trash (1)" }),
  );

  await waitFor(() =>
    expect(api.trashRuns).toHaveBeenCalledWith(["run-1"]),
  );
  expect(window.confirm).toHaveBeenCalledWith(
    expect.stringContaining("Estimated permanent cleanup"),
  );

  fireEvent.change(screen.getByLabelText("Search runs"), {
    target: { value: "nvidia" },
  });
  fireEvent.change(screen.getByLabelText("Status"), {
    target: { value: "succeeded" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));

  await waitFor(() =>
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/runs?q=nvidia&status=succeeded",
    ),
  );
  await waitFor(() => {
    const lastQuery = vi.mocked(api.runs).mock.calls.at(-1)?.[0] ?? "";
    const params = new URLSearchParams(lastQuery.replace(/^\?/, ""));
    expect(Object.fromEntries(params)).toMatchObject({
      trash_state: "active",
      q: "nvidia",
      status: "succeeded",
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
  vi.mocked(api.runs).mockResolvedValue(page([trashed], 20, 41));

  render(
    <Router initialPath="/runs?trash_state=trashed&offset=20">
      <Runs />
      <LocationProbe />
    </Router>,
  );

  await screen.findByText("NVIDIA Corporation");
  fireEvent.click(screen.getByLabelText("Select run NVDA"));
  fireEvent.click(
    screen.getByRole("button", { name: "Restore selected (1)" }),
  );
  await waitFor(() =>
    expect(api.restoreRuns).toHaveBeenCalledWith(["run-trashed"]),
  );
  expect(window.confirm).not.toHaveBeenCalled();
  await waitFor(() =>
    expect(screen.getByTestId("location")).toHaveTextContent(
      "/runs?trash_state=trashed&offset=0",
    ),
  );
});
