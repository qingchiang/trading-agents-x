import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import { api } from "../api/client";
import i18n from "../i18n";
import { Router } from "../router";
import ResearchLibrary from "./ResearchLibrary";
vi.mock("../api/client", () => ({ api: { timelines: vi.fn() } }));
beforeEach(async () => { vi.resetAllMocks(); await i18n.changeLanguage("en"); });
test("reads global filters and pagination from the URL and displays the primary judgment date", async () => {
  vi.mocked(api.timelines).mockResolvedValue({ total: 51, limit: 25, offset: 25, items: [{ instrument: "NVDA", instrument_name: "NVIDIA", full_cycle_count: 2, latest_analysis_date: "2026-09-04", primary_analysis_date: "2026-08-01", primary_rating: "Hold" }] });
  render(<Router initialPath="/timelines?q=nvid&offset=25&warning_only=true"><ResearchLibrary /></Router>);
  expect(await screen.findByText("NVIDIA")).toBeVisible();
  expect(api.timelines).toHaveBeenLastCalledWith(25, 25, "nvid", true);
  expect(screen.getByText("2026-08-01")).toBeVisible();
  expect(screen.getByText(/Latest research: 2026-09-04/)).toBeVisible();
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "toyota" } });
  await waitFor(() => expect(api.timelines).toHaveBeenLastCalledWith(25, 0, "toyota", true));
});
test("distinguishes failure from empty results and retries without dropping filters", async () => {
  vi.mocked(api.timelines).mockRejectedValueOnce(new Error("Offline")).mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 });
  render(<Router initialPath="/timelines?q=missing"><ResearchLibrary /></Router>);
  expect(await screen.findByRole("alert")).toHaveTextContent("Offline");
  expect(screen.queryByText("No matching research.")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Try again" }));
  expect(await screen.findByText("No matching research.")).toBeVisible();
  expect(api.timelines).toHaveBeenLastCalledWith(25, 0, "missing", false);
});

test("keeps existing results visible while a new search is pending and ignores older responses", async () => {
  const original = { total: 1, limit: 25, offset: 0, items: [{ instrument: "NVDA", instrument_name: "NVIDIA", full_cycle_count: 1, latest_analysis_date: "2026-07-24" }] };
  let finishOld!: (value: typeof original) => void;
  let finishNew!: (value: typeof original) => void;
  vi.mocked(api.timelines).mockResolvedValueOnce(original)
    .mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve; }))
    .mockImplementationOnce(() => new Promise(resolve => { finishNew = resolve; }));
  render(<Router initialPath="/timelines"><ResearchLibrary /></Router>);
  expect(await screen.findByText("NVIDIA")).toBeVisible();
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "old" } });
  await waitFor(() => expect(finishOld).toBeDefined());
  expect(screen.getByText("NVIDIA")).toBeVisible();
  expect(screen.getByRole("status")).toHaveTextContent("Updating results");
  fireEvent.change(screen.getByRole("searchbox"), { target: { value: "new" } });
  await waitFor(() => expect(finishNew).toBeDefined());
  await act(async () => finishNew({ ...original, items: [{ ...original.items[0], instrument_name: "New result" }] }));
  expect(await screen.findByText("New result")).toBeVisible();
  await act(async () => finishOld({ ...original, items: [{ ...original.items[0], instrument_name: "Old result" }] }));
  await waitFor(() => expect(screen.queryByText("Old result")).toBeNull());
});
