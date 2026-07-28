import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { api, type MemoryEntry } from "../api/client";
import i18n from "../i18n";
import Memory from "./Memory";

vi.mock("../api/client", () => ({
  api: {
    memory: vi.fn(),
  },
}));

const entry = {
  run_id: "legacy-run",
  ticker: "7203.T",
  market: "Asia/Tokyo",
  asset_type: "stock",
  analysis_date: "2026-07-24",
  decision: {
    rating: "Hold",
    confidence: 0.6,
    thesis: "**Imported thesis** with <script>unsafe()</script> markup.",
    evidence_refs: [],
    catalysts: [],
    risks: [],
    invalidation_conditions: [],
    time_horizon: "6-12 months",
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
});

test("renders imported thesis and reflection as sanitized Markdown", async () => {
  const { container } = render(<Memory />);

  expect((await screen.findByText("Imported thesis")).tagName).toBe("STRONG");
  expect(
    screen.getByRole("heading", { name: "Imported reflection" }),
  ).toBeInTheDocument();
  expect(screen.getByText("Preserve the lesson.")).toBeInTheDocument();
  expect(container.querySelector("script")).toBeNull();
  expect(container.textContent).not.toContain("<script>");
});

test("submits fuzzy and full-field filters without rewriting ticker case", async () => {
  render(<Memory />);
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

  const { container } = render(<Memory />);

  await waitFor(() =>
    expect(api.memory).toHaveBeenCalledWith("?q=legacy-run"),
  );
  await waitFor(() =>
    expect(container.querySelector("#memory-legacy-run")).not.toBeNull(),
  );
  const card = container.querySelector<HTMLElement>("#memory-legacy-run");
  await waitFor(() => expect(document.activeElement).toBe(card));
});
