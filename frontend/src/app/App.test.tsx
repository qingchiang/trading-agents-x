import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { api, type Capabilities, type Health, type RunPage } from "../shared/api/client";
import App from "./App";
import i18n from "../shared/i18n";
import { Router } from "./router";

vi.mock("../shared/api/client", () => ({
  api: {
    capabilities: vi.fn(),
    health: vi.fn(),
    runs: vi.fn(),
    runGroups: vi.fn(),
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
    models: { quick: { connection_id: "openai", model: "quick", reasoning_effort: "provider_default" }, deep: { connection_id: "openai", model: "deep", reasoning_effort: "provider_default" } },
    output_language: "en",
    lan_enabled: false,
    trash_retention_days: 30,
  },
  analysts: ["market", "social", "news", "fundamentals"],
  profiles: ["fast", "standard", "deep"],
  output_languages: ["en", "zh-CN", "ja"],
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
  vi.mocked(api.runGroups).mockResolvedValue({ items: [], total: 0, limit: 12, offset: 0 });
  vi.mocked(api.capabilities).mockResolvedValue(capabilities);
});

test("retired Memory route and navigation are unavailable while Runs remains usable", async () => {
  render(
    <Router initialPath="/memory">
      <App />
    </Router>,
  );

  expect(await screen.findByRole("heading", { name: "Page not found" })).toBeVisible();
  expect(screen.queryByRole("heading", { name: "Memory" })).not.toBeInTheDocument();
  expect(screen.queryByRole("link", { name: "Memory" })).not.toBeInTheDocument();

  const runsNavigation = screen.getByRole("link", {
    name: /^Run tasks$/,
  });
  expect(runsNavigation).toHaveAttribute("href", "/runs");
  fireEvent.click(runsNavigation);

  expect(await screen.findByRole("heading", { name: "Run tasks" })).toBeVisible();
  expect(screen.getByRole("link", { name: /^Run tasks$/ })).toHaveClass(
    "active",
  );
  expect(screen.queryByRole("heading", { name: "Memory" })).not.toBeInTheDocument();
});
