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
  expect(screen.getByText("トヨタ自動車")).toHaveClass("instrument-primary-name");
  expect(screen.getByText("Toyota Motor Corporation")).toHaveClass(
    "instrument-alternate-name",
  );
  expect(screen.getByText("7203.T")).toHaveClass("instrument-ticker");
});

test("uses the ticker as the primary identity only when names are unavailable", () => {
  render(<InstrumentIdentity ticker="NVDA" />);

  expect(screen.getByText("NVDA")).toHaveClass("instrument-primary-name");
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
