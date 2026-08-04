import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";

import { InstrumentIdentity } from "./Instruments";

test("shows a distinct local name before the general name", () => {
  render(
    <InstrumentIdentity
      ticker="7203.T"
      instrumentLocalName="トヨタ自動車"
      instrumentName="Toyota Motor Corporation"
    />,
  );

  expect(screen.getByText("7203.T")).toBeVisible();
  expect(screen.getByText("トヨタ自動車")).toBeVisible();
  expect(screen.getByText("Toyota Motor Corporation")).toBeVisible();
});

test("deduplicates equivalent local and general names", () => {
  render(
    <InstrumentIdentity
      ticker="NVDA"
      instrumentLocalName="NVIDIA, Inc."
      instrumentName="nvidia inc"
    />,
  );

  expect(screen.getByText("NVIDIA, Inc.")).toBeVisible();
  expect(screen.queryByText("nvidia inc")).not.toBeInTheDocument();
});
