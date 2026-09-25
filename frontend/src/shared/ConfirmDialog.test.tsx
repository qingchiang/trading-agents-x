import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { expect, it } from "vitest";
import ConfirmDialog from "./ConfirmDialog";

it("keeps keyboard focus in the confirmation and restores the trigger on close", () => {
  function Example() {
    const [open, setOpen] = useState(false);
    return <><button onClick={() => setOpen(true)}>Manage cycle</button>{open &&
      <ConfirmDialog title="Move cycle?" confirmLabel="Move" cancelLabel="Keep"
        onConfirm={() => setOpen(false)} onCancel={() => setOpen(false)}>All updates are included.</ConfirmDialog>}</>;
  }
  render(<Example />);
  const trigger = screen.getByText("Manage cycle");
  trigger.focus();
  fireEvent.click(trigger);
  const last = screen.getByRole("button", { name: /^Move$/ });
  last.focus();
  fireEvent.keyDown(last, { key: "Tab" });
  expect(screen.getByRole("button", { name: "Keep" })).toHaveFocus();
  fireEvent.keyDown(document.activeElement!, { key: "Escape" });
  expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});
