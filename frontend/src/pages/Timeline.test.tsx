import { render, screen } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { api } from "../api/client";
import i18n from "../i18n";
import { Router } from "../router";
import Timeline from "./Timeline";

vi.mock("../api/client", () => ({ api: { timeline: vi.fn(), timelines: vi.fn() } }));

beforeEach(async () => {
  vi.resetAllMocks();
  await i18n.changeLanguage("en");
});

test("shows the first Full Run-backed node and keeps its operational Run link", async () => {
  vi.mocked(api.timeline).mockResolvedValue({
    timeline: {
      instrument: "7203.T",
      primary_cycle_id: "run-1",
      nodes: [
        {
          id: "run-1",
          cycle_id: "run-1",
          instrument: "7203.T",
          analysis_date: "2026-07-24",
          research_schema_version: "1",
          information_cutoff_at: "2026-07-24T14:59:59Z",
          method_snapshot: { llm_provider: "fixture" },
          is_primary: true,
          trashed_at: null,
        },
      ],
    },
  } as never);

  render(
    <Router initialPath="/timelines/7203.T">
      <Timeline />
    </Router>,
  );

  expect(await screen.findByText("Primary Cycle")).toBeVisible();
  expect(screen.getByText("run-1")).toBeVisible();
  expect(
    screen.getByRole("link", { name: "Open operational Run →" }),
  ).toHaveAttribute("href", "/runs/run-1");
});

test("lists derived timelines without presenting Execution History as a timeline", async () => {
  vi.mocked(api.timelines).mockResolvedValue({
    items: [{ instrument: "7203.T", primary_cycle_id: "run-1", node_count: 1 }],
    total: 1,
  } as never);

  render(
    <Router initialPath="/timelines">
      <Timeline />
    </Router>,
  );

  expect(await screen.findByRole("heading", { name: "Research Timelines" })).toBeVisible();
  expect(screen.getByRole("link", { name: "7203.T" })).toHaveAttribute(
    "href",
    "/timelines/7203.T",
  );
  expect(screen.getByText("1 research node")).toBeVisible();
});
