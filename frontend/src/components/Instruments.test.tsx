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

  const ticker = screen.getByText("7203.T");
  const localName = screen.getByText("トヨタ自動車");
  const generalName = screen.getByText("Toyota Motor Corporation");

  expect(ticker).toBeVisible();
  expect(localName).toHaveClass("instrument-primary-name");
  expect(generalName).toHaveClass("instrument-secondary-name");
  expect(ticker.parentElement).toBe(localName.parentElement);
  expect(generalName.parentElement).toBe(ticker.parentElement);
  expect(localName).toHaveAttribute("title", "トヨタ自動車");
  expect(generalName).toHaveAttribute("title", "Toyota Motor Corporation");
});

test("keeps the only available name on the primary line", () => {
  const { container } = render(
    <InstrumentIdentity ticker="GOOG" instrumentName="Alphabet Inc." />,
  );

  expect(screen.getByText("Alphabet Inc.")).toHaveClass(
    "instrument-primary-name",
  );
  expect(container.querySelector(".instrument-secondary-name")).toBeNull();
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
