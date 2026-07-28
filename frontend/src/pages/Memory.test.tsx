import { render, screen } from "@testing-library/react";
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
