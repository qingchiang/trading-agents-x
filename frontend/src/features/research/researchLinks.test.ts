import { expect, test } from "vitest";
import type { RunSummaryView } from "../../shared/api/client";
import { legacyResearchLocation, researchLocation } from "./researchLinks";

const run = { id: "historic", is_research_node: true, request: { ticker: "TEST" }, research_kind: "incremental", trashed_at: "2026-01-01" } as RunSummaryView;
test("normalizes explicit reading links without losing the exact node or citation context", () => {
  expect(legacyResearchLocation(run, "?view=incremental&ref=ev_a&return_view=reports&report=market", "#source")).toBe("/timelines/TEST?view=brief&ref=ev_a&return_view=reports&report=market&node=historic&trash_state=all#source");
  expect(legacyResearchLocation(run, "", "")).toBeNull();
  expect(legacyResearchLocation(run, "?view=timeline", "")).toBeNull();
  expect(legacyResearchLocation(run, "?view=diagnostics", "")).toBeNull();
  expect(legacyResearchLocation({ ...run, is_research_node: false }, "?view=decision", "")).toBeNull();
  expect(researchLocation(run)).toContain("node=historic");
});
