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
  expect(localName.tagName).toBe("STRONG");
  expect(ticker.tagName).toBe("SPAN");
  const names = localName.closest(".instrument-names");
  const identity = ticker.closest(".instrument-identity");
  expect(names).not.toBeNull();
  expect(generalName.parentElement).toBe(names);
  expect(identity).not.toBeNull();
  expect(Array.from(identity!.children)).toEqual([names, ticker]);
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

test("uses the preferred name as the prominent page heading", () => {
  render(
    <InstrumentIdentity
      ticker="7203.T"
      instrumentLocalName="トヨタ自動車"
      instrumentName="Toyota Motor Corporation"
      prominent
    />,
  );

  expect(
    screen.getByRole("heading", { level: 1, name: "トヨタ自動車" }),
  ).toBeVisible();
  expect(screen.getByText("7203.T").tagName).toBe("SPAN");
  expect(screen.getByText("Toyota Motor Corporation").tagName).toBe("SPAN");
});

test("falls back to the ticker as the prominent heading without names", () => {
  render(<InstrumentIdentity ticker="GOOG" prominent />);

  expect(screen.getByRole("heading", { level: 1, name: "GOOG" })).toBeVisible();
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
