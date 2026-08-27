import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { api, type Capabilities, type Health, type RunPage } from "./api/client";
import App from "./App";
import i18n from "./i18n";
import { Router } from "./router";

vi.mock("./api/client", () => ({
  api: {
    capabilities: vi.fn(),
    health: vi.fn(),
    runs: vi.fn(),
  },
}));

const emptyRunPage = {
  items: [],
  total: 0,
  limit: 20,
  offset: 0,
} as RunPage;

const capabilities = {
  defaults: {
    profile: "standard",
    llm_provider: "openai",
    quick_model: "quick",
    deep_model: "deep",
    quick_reasoning_effort: "provider_default",
    deep_reasoning_effort: "provider_default",
    output_language: "en",
    lan_enabled: false,
    trash_retention_days: 30,
  },
  providers: {},
} as Capabilities;

beforeEach(async () => {
  vi.resetAllMocks();
  await i18n.changeLanguage("en");
  vi.mocked(api.health).mockResolvedValue({
    status: "ok",
    database: "ok",
    queue: { queued: 0, running: 0 },
    version: "0.5.0",
  } as Health);
  vi.mocked(api.runs).mockResolvedValue(emptyRunPage);
  vi.mocked(api.capabilities).mockResolvedValue(capabilities);
});

test("retired Memory route and navigation are unavailable while Runs remains usable", async () => {
  render(
    <Router initialPath="/memory">
      <App />
    </Router>,
  );

  expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeVisible();
  expect(screen.queryByRole("heading", { name: "Memory" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Memory" })).not.toBeInTheDocument();

  const runsNavigation = screen.getByRole("link", {
    name: /^Runs$/,
  });
  expect(runsNavigation).toHaveAttribute("href", "/runs");
  fireEvent.click(runsNavigation);

  expect(await screen.findByRole("heading", { name: "Runs" })).toBeVisible();
  expect(screen.getByRole("link", { name: /^Runs$/ })).toHaveClass(
    "active",
  );
  expect(screen.queryByRole("heading", { name: "Memory" })).not.toBeInTheDocument();
});
