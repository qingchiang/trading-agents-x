import { fireEvent, render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import { ActionMenu } from "./Interaction";

test("leaves navigation keys to the browser and restores menu focus on Escape and selection", () => {
  render(<ActionMenu label="Manage cycle"><button>Restore</button><button>Delete</button></ActionMenu>);
  const trigger = screen.getByRole("button", { name: "Manage cycle" });
  trigger.focus();
  for (const key of ["Home", "End", "ArrowDown", "ArrowUp", "ArrowLeft", "ArrowRight"]) {
    expect(fireEvent.keyDown(trigger, { key })).toBe(true);
    expect(screen.queryByRole("button", { name: "Restore" })).toBeNull();
  }
  fireEvent.click(trigger);
  const action = screen.getByRole("button", { name: "Restore" });
  action.focus();
  expect(fireEvent.keyDown(action, { key: "End" })).toBe(true);
  expect(action).toHaveFocus();
  fireEvent.keyDown(action, { key: "Escape" });
  expect(trigger).toHaveFocus();
  fireEvent.click(trigger);
  fireEvent.click(screen.getByRole("button", { name: "Restore" }));
  expect(trigger).toHaveFocus();
  expect(screen.queryByRole("button", { name: "Restore" })).toBeNull();
});
