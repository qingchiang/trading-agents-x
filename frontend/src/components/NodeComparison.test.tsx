import { fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import { api, type ResearchNodeComparison } from "../api/client";
import i18n from "../i18n";
import { Router } from "../router";
import NodeComparison from "./NodeComparison";
vi.mock("../api/client", () => ({ api: { evidence: vi.fn() } }));
beforeEach(async () => { vi.resetAllMocks(); await i18n.changeLanguage("en"); });
const comparison: ResearchNodeComparison = {
  instrument: "NVDA", cross_cycle: true, method_changed: false,
  sides: ["left", "right"].map((id, index) => ({ node_id: id, cycle_id: id, lifecycle_state: "active", research_kind: "full", research_schema_version: "2", analysis_date: `2026-07-${20 + index}`, method_snapshot: {}, decision: {} })),
  decision_sections: [{ key: "thesis", values: [{ state: "recorded", value: "**Left thesis**[^ev_0123456789ab]" }, { state: "recorded", value: "**Right thesis**[^ev_0123456789ab]" }] }, { key: "future_internal_field", values: [{ state: "recorded", value: { raw_identifier: "private-marker" } }, { state: "null" }] }],
};
test("reads comparison on the page and resolves each side's evidence independently", async () => {
  vi.mocked(api.evidence).mockImplementation(async id => ({ version: "8", instrument: "NVDA", analysis_date: "2026-07-20", items: [{ ref: "ev_0123456789ab", source: id, evidence_type: "news", requested_date: "2026-07-20", content: `${id} source content` }] }));
  render(<Router initialPath="/timelines/NVDA?view=compare"><NodeComparison comparison={comparison} onClose={() => {}} /></Router>);
  expect(screen.queryByRole("dialog")).toBeNull();
  const article = screen.getByRole("region", { name: "Node Comparison" });
  expect(within(article).queryByText("private-marker")).toBeNull();
  await waitFor(() => expect(screen.queryByText("Loading evidence…")).toBeNull());
  await waitFor(() => expect(screen.getAllByText("[E01]")).toHaveLength(2));
  const refs = screen.getAllByRole("button", { name: /Open evidence/ });
  fireEvent.click(refs[1]);
  expect(await within(await screen.findByRole("dialog")).findByText("right source content")).toBeVisible();
});

test("keeps partial evidence readable and retries only the failed direct baseline", async () => {
  const fixture = structuredClone(comparison);
  fixture.sides[1] = { ...fixture.sides[1], research_kind: "incremental", cycle_id: "baseline" };
  let failed = true;
  vi.mocked(api.evidence).mockImplementation(async id => {
    if (id === "baseline" && failed) throw new Error("Offline");
    return { version: "8", instrument: "NVDA", analysis_date: "2026-07-20", items: id === "right" ? [] : [{ ref: "ev_0123456789ab", source: id, evidence_type: "news", requested_date: "2026-07-20", content: `${id} source content` }] };
  });
  render(<Router initialPath="/timelines/NVDA?view=compare"><NodeComparison comparison={fixture} onClose={() => {}} /></Router>);
  const retry = await screen.findByRole("button", { name: "Try again" });
  expect(screen.getByText("Right thesis")).toBeVisible();
  failed = false;
  fireEvent.click(retry);
  await waitFor(() => expect(screen.queryByRole("alert")).toBeNull());
  const reference = screen.getAllByRole("button", { name: /Open evidence/ })[1];
  fireEvent.click(reference);
  expect(await within(await screen.findByRole("dialog")).findByText("baseline source content")).toBeVisible();
  expect(vi.mocked(api.evidence).mock.calls.filter(([id]) => id === "right")).toHaveLength(1);
  expect(vi.mocked(api.evidence).mock.calls.filter(([id]) => id === "baseline")).toHaveLength(2);
});

test("does not call unknown scenario content unchanged or expose its raw fields", async () => {
  vi.mocked(api.evidence).mockResolvedValue({ version: "8", instrument: "NVDA", analysis_date: "2026-07-20", items: [] });
  const fixture = structuredClone(comparison);
  fixture.decision_sections = [{ key: "scenarios", values: [
    { state: "recorded", value: [{ kind: "base", outcome: "Same", core_assumptions: [], private_marker: "before" }] },
    { state: "recorded", value: [{ kind: "base", outcome: "Same", core_assumptions: [], private_marker: "after" }] },
  ] }];
  render(<Router initialPath="/timelines/NVDA?view=compare"><NodeComparison comparison={fixture} onClose={() => {}} /></Router>);
  expect(await screen.findByText("Additional recorded fields are available in run diagnostics.")).toBeVisible();
  expect(screen.queryByText("private_marker")).toBeNull();
  expect(screen.queryByText("No changed sections in this group.")).toBeNull();
});
