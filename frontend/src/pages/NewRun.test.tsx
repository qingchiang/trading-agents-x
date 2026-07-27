import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import {
  api,
  type Capabilities,
  type RunView,
} from "../api/client";
import i18n from "../i18n";
import { Router, usePathname } from "../router";
import NewRun from "./NewRun";

vi.mock("../api/client", () => ({
  api: {
    capabilities: vi.fn(),
    createRun: vi.fn(),
  },
}));

const capabilities = {
  profiles: ["fast", "standard", "deep"],
  analysts: ["market", "social", "news", "fundamentals"],
  output_languages: ["en", "zh-Hans", "ja"],
  providers: {
    openai: {
      quick_models: [{ label: "Quick", value: "gpt-5.4-mini" }],
      deep_models: [{ label: "Deep", value: "gpt-5.5" }],
      reasoning_efforts: {
        "gpt-5.4-mini": ["provider_default", "low", "high"],
        "gpt-5.5": ["provider_default", "medium", "high"],
      },
      api_key_configured: true,
    },
  },
  defaults: {
    profile: "standard",
    llm_provider: "openai",
    quick_model: "gpt-5.4-mini",
    deep_model: "gpt-5.5",
    output_language: "English",
    lan_enabled: false,
  },
} as Capabilities;

function NewRunRoutes() {
  const pathname = usePathname();
  return pathname === "/runs/new" ? <NewRun /> : <div>Run opened</div>;
}

beforeEach(async () => {
  vi.resetAllMocks();
  await i18n.changeLanguage("en");
  vi.mocked(api.capabilities).mockResolvedValue(capabilities);
});

test("reuses the idempotency key when a browser submission is retried", async () => {
  vi.mocked(api.createRun)
    .mockRejectedValueOnce(new Error("temporary network error"))
    .mockResolvedValueOnce({ id: "run-2" } as RunView);
  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );
  await screen.findByRole("option", { name: "Quick" });
  fireEvent.change(screen.getByLabelText(/^Ticker/), {
    target: { value: "NVDA" },
  });

  fireEvent.click(screen.getByRole("button", { name: /Queue research/ }));
  await screen.findByText("temporary network error");
  fireEvent.click(screen.getByRole("button", { name: /Queue research/ }));

  await screen.findByText("Run opened");
  expect(api.createRun).toHaveBeenCalledTimes(2);
  const firstKey = vi.mocked(api.createRun).mock.calls[0][1];
  const secondKey = vi.mocked(api.createRun).mock.calls[1][1];
  expect(secondKey).toBe(firstKey);
  expect(vi.mocked(api.createRun).mock.calls[1][0]).toMatchObject({
    ticker: "NVDA",
    quick_reasoning_effort: "provider_default",
    deep_reasoning_effort: "provider_default",
  });
});

test("keeps UI locale and report output language independent", async () => {
  vi.mocked(api.createRun).mockResolvedValue({ id: "run-3" } as RunView);
  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );
  await screen.findByRole("option", { name: "Quick" });
  fireEvent.change(screen.getByLabelText(/^Ticker/), {
    target: { value: "7203.T" },
  });
  fireEvent.change(screen.getByLabelText(/^Report language/), {
    target: { value: "ja" },
  });
  fireEvent.click(screen.getByRole("button", { name: /Queue research/ }));

  await waitFor(() => expect(api.createRun).toHaveBeenCalled());
  expect(vi.mocked(api.createRun).mock.calls[0][0].output_language).toBe("ja");
  expect(i18n.language).toBe("en");
});
