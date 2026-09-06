import { expect, test } from "@playwright/test";

test("restores library filters and resolves an off-page historical node explicitly", async ({ page }) => {
  const queries: string[] = [];
  await page.addInitScript(() => localStorage.setItem("tradingagents-locale", "en"));
  await page.route("**/api/v1/**", async route => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/v1/timelines") {
      queries.push(url.search);
      const offset = Number(url.searchParams.get("offset"));
      const q = url.searchParams.get("q") ?? "";
      return route.fulfill({ json: { limit: 25, offset, total: q ? 1 : 26, items: [{ instrument: offset ? "MSFT" : "NVDA", instrument_name: offset ? "Microsoft" : "NVIDIA", primary_analysis_date: "2026-07-20", latest_analysis_date: "2026-07-25", primary_rating: "Hold", full_cycle_count: 13 }] } });
    }
    if (url.pathname === "/api/v1/timelines/NVDA") {
      const focus = url.searchParams.get("focus_node_id");
      if (focus === "missing") return route.fulfill({ status: 404, json: { error: { code: "run_not_found", message: "Requested research node is not available for this instrument" } } });
      const baseline = { id: "historical", cycle_id: "historical", instrument: "NVDA", research_kind: "full", analysis_date: "2026-07-20", is_active: true, is_primary: false, is_cycle_head: true, full_baseline_run_id: null, method_snapshot: {} };
      return route.fulfill({ json: { timeline: { instrument: "NVDA", instrument_name: "NVIDIA", primary_cycle_id: "primary", cycle_limit: 12, cycle_offset: 12, cycle_total: 13, active_full_cycles: [{ id: "primary", analysis_date: "2026-07-25" }], cycles: [{ id: "historical", baseline, increments: [], head_run_id: "historical", is_primary: false }] } } });
    }
    if (url.pathname === "/api/v1/runs/historical") return route.fulfill({ json: { run: { id: "historical", status: "succeeded", research_kind: "full", request: { ticker: "NVDA", analysis_date: "2026-07-20", analysts: [] }, metrics: {}, attempt: 1 }, result: null, attempts: [], evidence_status: { status: "pending" } } });
    if (url.pathname.includes("analysis-cutoff-context")) return route.fulfill({ json: { max_analysis_date: "2026-07-26", observed_at: "2026-07-26T00:00:00Z", valid_until: "2026-07-27T00:00:00Z" } });
    return route.fulfill({ status: 404, json: { detail: "Not mocked" } });
  });
  await page.goto("/timelines");
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.getByText("Microsoft", { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/offset=25/);
  await page.getByRole("searchbox").fill("nvda");
  await expect(page.getByText("NVIDIA", { exact: true })).toBeVisible();
  await expect(page).not.toHaveURL(/offset=25/);
  expect(queries.at(-1)).toContain("q=nvda");
  await page.goto("/timelines/NVDA?node=historical");
  await expect(page).toHaveURL(/node=historical&cycle_offset=12/);
  await expect(page.locator(".workspace-context")).toContainText("Full baseline: 2026-07-20");
  await expect(page.locator(".workspace-context")).toContainText("Selected research");
  await page.goto("/timelines/NVDA?node=missing");
  await expect(page.getByRole("alert")).toContainText("Requested research node is not available");
  await expect(page.locator(".run-reader")).toHaveCount(0);
});
