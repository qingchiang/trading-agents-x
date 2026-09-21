import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import type { ResearchCycleView } from "../../shared/api/client";
import i18n from "../../shared/i18n";
import CycleHistory from "./CycleHistory";

test("separates the cycle title, disclosure and baseline reading action", async () => {
  await i18n.changeLanguage("en");
  const onSelect = vi.fn();
  const cycle = { id: "base", is_primary: true, baseline: { id: "base", analysis_date: "2026-08-24", research_kind: "full", is_active: true }, increments: [] } as unknown as ResearchCycleView;
  render(<CycleHistory cycles={[cycle]} onSelect={onSelect} />);
  const heading = screen.getByRole("heading", { name: /Research cycle/ });
  fireEvent.click(heading);
  expect(onSelect).not.toHaveBeenCalled();
  const baseline = screen.getByRole("button", { name: /Full baseline/i });
  expect(baseline).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: /Collapse cycle/ }));
  expect(baseline).not.toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: /Expand cycle/ }));
  fireEvent.click(baseline);
  expect(onSelect).toHaveBeenCalledWith("base");
});
