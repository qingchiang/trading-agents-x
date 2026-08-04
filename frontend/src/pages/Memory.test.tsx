import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { api, type MemoryEntry } from "../api/client";
import i18n from "../i18n";
import { Router } from "../router";
import Memory from "./Memory";

vi.mock("../api/client", () => ({
  api: {
    memory: vi.fn(),
    recentInstruments: vi.fn(),
  },
}));

const entry = {
  run_id: "legacy-run",
  ticker: "7203.T",
  instrument_name: "Toyota Motor Corporation",
  instrument_local_name: "トヨタ自動車",
  market: "Asia/Tokyo",
  asset_type: "stock",
  analysis_date: "2026-07-24",
  profile: "standard",
  decision: {
    rating: "Hold",
    confidence: 0.6,
    executive_summary: "Imported decision summary.",
    thesis: "**Imported thesis** with <script>unsafe()</script> markup.",
    evidence_refs: [],
    catalysts: ["**Volume growth** accelerates."],
    risks: ["Input costs remain elevated."],
    invalidation_conditions: ["Demand falls below the base case."],
    unresolved_questions: ["Will pricing remain durable?"],
    time_horizon: "6-12 months",
    scenarios: (["base", "bull", "bear"] as const).map((kind) => ({
      kind,
      core_assumptions: ["Imported assumptions remain valid."],
      outcome: `Imported ${kind} outcome.`,
      evidence_refs: [],
    })),
  },
  outcome: {
    status: "resolved",
    benchmark: "^N225",
    observation_start: "2026-07-25",
    observation_end: "2026-08-01",
    holding_intervals: 5,
    raw_return: 0.03,
    alpha_return: 0.01,
  },
  reflection: "### Imported reflection\n\n- Preserve the lesson.",
} as MemoryEntry;

beforeEach(async () => {
  vi.resetAllMocks();
  window.history.replaceState(null, "", "/memory");
  await i18n.changeLanguage("en");
  vi.mocked(api.memory).mockResolvedValue([entry]);
  vi.mocked(api.recentInstruments).mockResolvedValue([]);
});

function renderMemory() {
  return render(
    <Router>
      <Memory />
    </Router>,
  );
}

test("renders imported thesis and reflection as sanitized Markdown", async () => {
  const { container } = renderMemory();

  expect((await screen.findByText("Imported thesis")).tagName).toBe("STRONG");
  expect(
    screen.getByRole("heading", { name: "Imported reflection" }),
  ).toBeInTheDocument();
  expect(screen.getByText("Preserve the lesson.")).toBeInTheDocument();
  expect(container.querySelector("script")).toBeNull();
  expect(container.textContent).not.toContain("<script>");
  expect(screen.getByText("Toyota Motor Corporation")).toBeVisible();
  expect(screen.getByText("トヨタ自動車")).toBeVisible();
  expect(screen.getByText("Standard")).toHaveAttribute(
    "title",
    "Bull / bear → judge → risk",
  );
});

test("uses recent ticker suggestions and stable autocomplete fields", async () => {
  vi.mocked(api.recentInstruments).mockResolvedValue([
    {
      ticker: "7203.T",
      instrument_name: "Toyota Motor Corporation",
      instrument_local_name: "トヨタ自動車",
      last_used_at: "2026-07-28T00:00:00Z",
    },
  ]);
  renderMemory();

  const ticker = screen.getByLabelText("Ticker");
  expect(ticker).toHaveAttribute("id", "memory-ticker");
  expect(ticker).toHaveAttribute("name", "ticker");
  expect(ticker).toHaveAttribute("autocomplete", "on");
  expect(ticker).toHaveAttribute("list", "recent-instruments");
  await waitFor(() =>
    expect(
      document.querySelector(
        'datalist#recent-instruments option[value="7203.T"]',
      ),
    ).toHaveAttribute("label", "トヨタ自動車 · Toyota Motor Corporation"),
  );
});

test("submits fuzzy and full-field filters without rewriting ticker case", async () => {
  renderMemory();
  await screen.findByText("Imported thesis");

  fireEvent.change(screen.getByLabelText("Keyword search"), {
    target: { value: "demand lesson" },
  });
  fireEvent.change(screen.getByLabelText("Ticker"), {
    target: { value: "vd" },
  });
  fireEvent.change(screen.getByLabelText("Market"), {
    target: { value: "america/new" },
  });
  fireEvent.change(screen.getByLabelText("Status"), {
    target: { value: "resolved" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));

  await waitFor(() => expect(api.memory).toHaveBeenCalledTimes(2));
  const query = vi.mocked(api.memory).mock.calls[1][0] ?? "";
  const params = new URLSearchParams(query.replace(/^\?/, ""));
  expect(Object.fromEntries(params)).toEqual({
    q: "demand lesson",
    ticker: "vd",
    market: "america/new",
    status: "resolved",
  });
  expect(window.location.search).toBe(query);
});

test("restores a linked memory query and focuses the referenced record", async () => {
  window.history.replaceState(
    null,
    "",
    "/memory?q=legacy-run#memory-legacy-run",
  );

  const { container } = renderMemory();

  await waitFor(() =>
    expect(api.memory).toHaveBeenCalledWith("?q=legacy-run"),
  );
  await waitFor(() =>
    expect(container.querySelector("#memory-legacy-run")).not.toBeNull(),
  );
  const card = container.querySelector<HTMLElement>("#memory-legacy-run");
  await waitFor(() => expect(document.activeElement).toBe(card));
});

test("expands the full decision and navigates to its run conclusion", async () => {
  renderMemory();

  await screen.findByText("Imported thesis");
  expect(screen.getByText("Volume growth")).not.toBeVisible();
  fireEvent.click(screen.getByText("Decision details"));
  expect(screen.getByText("Volume growth")).toBeVisible();
  expect(screen.getByText("Input costs remain elevated.")).toBeVisible();
  expect(screen.getByText("Demand falls below the base case.")).toBeVisible();
  expect(screen.getByText("6-12 months")).toBeVisible();
  expect(screen.getByText("Imported base outcome.")).toBeVisible();
  expect(screen.getByText("Imported bull outcome.")).toBeVisible();
  expect(screen.getByText("Imported bear outcome.")).toBeVisible();
  expect(screen.getByText("Will pricing remain durable?")).toBeVisible();

  expect(
    screen.getByRole("link", {
      name: "Open research decision 7203.T",
    }),
  ).toHaveAttribute("href", "/runs/legacy-run?view=decision");
  fireEvent.click(
    screen.getByRole("link", {
      name: "Open research decision",
    }),
  );
  expect(`${window.location.pathname}${window.location.search}`).toBe(
    "/runs/legacy-run?view=decision",
  );
});
