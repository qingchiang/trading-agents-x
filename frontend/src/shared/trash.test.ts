import { expect, test } from "vitest";

import { trashDeadline } from "./trash";

test("computes UTC trash deadlines and remaining calendar days", () => {
  const deadline = trashDeadline(
    "2026-07-01T00:00:00Z",
    30,
    new Date("2026-07-29T00:00:00Z"),
  );

  expect(deadline?.deletionAt.toISOString()).toBe("2026-07-31T00:00:00.000Z");
  expect(deadline?.remainingDays).toBe(2);
  expect(deadline?.due).toBe(false);
  expect(
    trashDeadline(
      "2026-07-01T00:00:00Z",
      30,
      new Date("2026-08-01T00:00:00Z"),
    )?.due,
  ).toBe(true);
  expect(trashDeadline("2026-07-01T00:00:00Z", 0)).toBeNull();
});
