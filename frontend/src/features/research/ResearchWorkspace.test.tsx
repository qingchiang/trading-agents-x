import { act, fireEvent, render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import i18n from "../../shared/i18n";
import ResearchWorkspace, { WorkspaceNavigationButtons } from "./ResearchWorkspace";

test("keeps the compact drawer open when content height changes, and responds to width changes", async () => {
  await i18n.changeLanguage("en");
  let resize: ResizeObserverCallback = () => {};
  vi.stubGlobal("ResizeObserver", class { constructor(callback: ResizeObserverCallback) { resize = callback; } observe() {} disconnect() {} });
  const notify = (width: number) => act(() => resize([{ contentRect: { width } } as ResizeObserverEntry], {} as ResizeObserver));
  try {
    render(<ResearchWorkspace history={<p>History</p>}><div className="workspace-reader">Research<WorkspaceNavigationButtons /></div></ResearchWorkspace>);
    notify(800);
    const trigger = screen.getByRole("button", { name: "Research history" });
    trigger.focus(); fireEvent.click(trigger);
    expect(screen.getByRole("dialog")).toBeVisible();
    notify(800);
    expect(screen.getByRole("dialog")).toBeVisible();
    notify(1200);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByText("History")).toBeVisible();
    notify(800);
    expect(screen.getByText("History")).not.toBeVisible();
  } finally { vi.unstubAllGlobals(); }
});

test("keeps history available when the reader cannot load", async () => {
  await i18n.changeLanguage("en");
  render(<ResearchWorkspace history={<p>Available history</p>}><p role="alert">Reader unavailable</p></ResearchWorkspace>);
  fireEvent.click(screen.getByRole("button", { name: "Research history" }));
  expect(screen.getByRole("dialog")).toHaveTextContent("Available history");
});
