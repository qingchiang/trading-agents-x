import { expect, test } from "vitest";
import type { RunSummaryView } from "../../shared/api/client";
import { researchLocation } from "./researchLinks";

test("links retained research to its exact Timeline node and trash scope", () => {
  const run = {id: "historic", request: {ticker: "TEST"}, trashed_at: "2026-01-01"} as RunSummaryView;
  expect(researchLocation(run)).toBe("/timelines/TEST?node=historic&trash_state=all");
  expect(researchLocation({...run, trashed_at: null})).toBe("/timelines/TEST?node=historic");
});
