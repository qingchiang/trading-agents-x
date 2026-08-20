import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, test } from "vitest";

import i18n from "../i18n";
import { Router } from "../router";
import Layout from "./Layout";

beforeEach(async () => {
  localStorage.removeItem("tradingagents-sidebar-collapsed");
  await i18n.changeLanguage("en");
});

function renderLayout(initialPath = "/") {
  return render(
    <Router initialPath={initialPath}>
      <Layout>
        <div>content</div>
      </Layout>
    </Router>,
  );
}

test("distinguishes new-run and run-management navigation", () => {
  const newRun = renderLayout("/runs/new");
  expect(screen.getByRole("link", { name: "New run" })).toHaveClass("active");
  expect(screen.getByRole("link", { name: "Runs" })).not.toHaveClass("active");
  newRun.unmount();

  renderLayout("/runs/run-1");
  expect(screen.getByRole("link", { name: "Runs" })).toHaveClass("active");
  expect(screen.getByRole("link", { name: "New run" })).not.toHaveClass(
    "active",
  );
});

test("shows the concise Simplified Chinese locale label", () => {
  renderLayout();

  expect(screen.getByRole("option", { name: "简体中文" })).toHaveValue(
    "zh-CN",
  );
  expect(
    screen.queryByRole("option", { name: /中国大陆/ }),
  ).not.toBeInTheDocument();
});

test("persists the desktop narrow-rail preference", () => {
  const first = renderLayout();

  fireEvent.click(
    screen.getByRole("button", { name: "Collapse sidebar" }),
  );
  expect(first.container.querySelector(".app-shell")).toHaveClass(
    "sidebar-collapsed",
  );
  expect(localStorage.getItem("tradingagents-sidebar-collapsed")).toBe(
    "true",
  );
  first.unmount();

  const restored = renderLayout();
  expect(restored.container.querySelector(".app-shell")).toHaveClass(
    "sidebar-collapsed",
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Expand sidebar" }),
  );
  expect(restored.container.querySelector(".app-shell")).not.toHaveClass(
    "sidebar-collapsed",
  );
});

test("closes the mobile drawer after navigation, backdrop, or Escape", () => {
  const { container } = renderLayout();
  const shell = container.querySelector(".app-shell");
  const open = () =>
    fireEvent.click(
      screen.getByRole("button", { name: "Open navigation" }),
    );

  open();
  expect(shell).toHaveClass("sidebar-open");
  fireEvent.click(screen.getByRole("link", { name: "Settings" }));
  expect(shell).not.toHaveClass("sidebar-open");

  open();
  fireEvent.click(
    container.querySelector(".sidebar-backdrop") as HTMLElement,
  );
  expect(shell).not.toHaveClass("sidebar-open");

  open();
  fireEvent.keyDown(window, { key: "Escape" });
  expect(shell).not.toHaveClass("sidebar-open");
});
