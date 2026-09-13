import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { expect, test } from "vitest";
import { ActionMenu, tabsKeyDown } from "./Interaction";

function TabsFixture() {
  const [selected, setSelected] = useState(0);
  return <><div role="tablist" onKeyDown={tabsKeyDown}>{["Overview", "Reports", "Evidence"].map((label, index) => <button key={label} role="tab" id={`tab-${index}`} aria-controls={`panel-${index}`} aria-selected={selected === index} tabIndex={selected === index ? 0 : -1} onClick={() => setSelected(index)}>{label}</button>)}</div>{[0, 1, 2].map(index => <section role="tabpanel" id={`panel-${index}`} aria-labelledby={`tab-${index}`} hidden={selected !== index} key={index} />)}</>;
}

test("moves tab selection, focus and its visible panel together", () => {
  render(<TabsFixture />);
  screen.getByRole("tab", { name: "Overview" }).focus();
  fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });
  expect(screen.getByRole("tab", { name: "Reports" })).toHaveFocus();
  expect(screen.getByRole("tabpanel", { name: "Reports" })).toBeVisible();
  fireEvent.keyDown(document.activeElement!, { key: "End" });
  expect(screen.getByRole("tab", { name: "Evidence" })).toHaveAttribute("aria-selected", "true");
  fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });
  expect(screen.getByRole("tab", { name: "Overview" })).toHaveFocus();
});

test("navigates a menu by keyboard and restores focus after selection or Escape", async () => {
  render(<ActionMenu label="Manage cycle"><button>Restore</button><button>Delete</button></ActionMenu>);
  const trigger = screen.getByRole("button", { name: "Manage cycle" });
  trigger.focus();
  fireEvent.keyDown(trigger, { key: "ArrowDown" });
  await waitFor(() => expect(screen.getByRole("button", { name: "Restore" })).toHaveFocus());
  fireEvent.keyDown(document.activeElement!, { key: "End" });
  expect(screen.getByRole("button", { name: "Delete" })).toHaveFocus();
  fireEvent.keyDown(document.activeElement!, { key: "Escape" });
  expect(trigger).toHaveFocus();
  fireEvent.click(trigger);
  const action = screen.getByRole("button", { name: "Restore" });
  action.focus(); fireEvent.click(action);
  expect(trigger).toHaveFocus();
  expect(screen.queryByRole("button", { name: "Restore" })).toBeNull();
});
