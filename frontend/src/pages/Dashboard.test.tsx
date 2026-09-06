import { render, screen, within } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import { api } from "../api/client";
import i18n from "../i18n";
import { Router } from "../router";
import Dashboard from "./Dashboard";
vi.mock("../api/client", () => ({ api: { health: vi.fn(), runs: vi.fn(), timelines: vi.fn() } }));
beforeEach(async () => {
  vi.resetAllMocks(); await i18n.changeLanguage("en");
  vi.mocked(api.health).mockResolvedValue({ status: "ok", database: "ok", queue: { queued: 0, running: 0 }, version: "test" });
  vi.mocked(api.runs).mockResolvedValue({ items: [], total: 0, limit: 4, offset: 0 });
  vi.mocked(api.timelines).mockImplementation(async (_limit, _offset, _q, warnings) => ({ items: warnings ? [] : [{ instrument: "7203.T", instrument_name: "Toyota Motor Corporation", instrument_local_name: "トヨタ自動車", full_cycle_count: 2, latest_analysis_date: "2026-07-24", primary_analysis_date: "2026-07-22", primary_baseline_date: "2026-07-20", primary_rating: "Hold", primary_confidence: "medium", primary_cycle_id: "baseline", primary_head_run_id: "head", primary_thesis: "The current primary assessment remains balanced.", latest_completed_cycle_id: "other", latest_completed_run_id: "other-head", latest_completed_analysis_date: "2026-07-24" }], total: warnings ? 0 : 1, limit: 6, offset: 0 }));
});
test("continues recent instrument work without confusing another cycle with the primary judgment", async () => {
  render(<Router initialPath="/"><Dashboard /></Router>);
  const name = await screen.findByText("Toyota Motor Corporation");
  const card = name.closest("article")!;
  expect(within(card).getByText("トヨタ自動車")).toBeVisible();
  expect(within(card).getByText("Hold")).toBeVisible();
  expect(card).toHaveTextContent("Current judgment as of: 2026-07-22");
  expect(card).toHaveTextContent("Full baseline: 2026-07-20");
  expect(card).toHaveTextContent("The current primary assessment remains balanced.");
  expect(within(card).getByRole("link", { name: /Recently completed in another cycle/ })).toHaveAttribute("href", "/timelines/7203.T?node=other-head");
  expect(within(card).getByRole("link", { name: "Update this research" })).toHaveAttribute("href", "/runs/new?intent=update&from_run=head&full_baseline_run_id=baseline");
  expect(api.timelines).toHaveBeenCalledWith(6, 0, "", false, "recent_activity");
});
test("keeps the primary summaries readable when the queue request fails", async () => {
  vi.mocked(api.health).mockRejectedValue(new Error("Queue unavailable"));
  render(<Router initialPath="/"><Dashboard /></Router>);
  expect(await screen.findByText("Toyota Motor Corporation")).toBeVisible();
  expect(await screen.findByRole("alert")).toHaveTextContent("Queue unavailable");
});
