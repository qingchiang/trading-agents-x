import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import {
  api,
  type Capabilities,
  type ProviderModelCatalog,
  type RunView,
} from "../api/client";
import i18n from "../i18n";
import { Router, usePathname } from "../router";
import NewRun from "./NewRun";

vi.mock("../api/client", () => ({
  api: {
    capabilities: vi.fn(),
    providerModels: vi.fn(),
    createRun: vi.fn(),
  },
}));

const capabilities = {
  profiles: ["fast", "standard", "deep"],
  analysts: ["market", "social", "news", "fundamentals"],
  output_languages: ["en", "zh-CN", "ja"],
  providers: {
    openai: {
      label: "OpenAI",
      api_key_required: true,
      api_key_configured: true,
      configured: true,
      selectable: true,
      unavailable_reason: null,
      model_discovery_supported: true,
    },
    anthropic: {
      label: "Anthropic",
      api_key_required: true,
      api_key_configured: false,
      configured: false,
      selectable: false,
      unavailable_reason: "api_key_missing",
      model_discovery_supported: true,
    },
    ollama: {
      label: "Ollama",
      api_key_required: false,
      api_key_configured: null,
      configured: true,
      selectable: true,
      unavailable_reason: null,
      model_discovery_supported: true,
    },
  },
  defaults: {
    profile: "standard",
    llm_provider: "openai",
    quick_model: "gpt-5.4-mini",
    deep_model: "gpt-5.5",
    quick_reasoning_effort: "low",
    deep_reasoning_effort: "high",
    output_language: "zh-CN",
    lan_enabled: false,
    archive_retention_days: 30,
  },
} as Capabilities;

const modelCatalog = {
  provider: "openai",
  models: [
    {
      id: "gpt-5.4-mini",
      label: "Quick",
      compatibility: "supported",
      reasoning_efforts: ["provider_default", "low", "high"],
      default_roles: ["quick"],
    },
    {
      id: "gpt-5.5",
      label: "Deep",
      compatibility: "supported",
      reasoning_efforts: ["provider_default", "medium", "high"],
      default_roles: ["deep"],
    },
    {
      id: "future-model",
      label: "Future model",
      compatibility: "unknown",
      reasoning_efforts: ["provider_default"],
      default_roles: [],
    },
  ],
  source: "live",
  fetched_at: "2026-07-28T00:00:00Z",
  stale: false,
  warning: null,
} as ProviderModelCatalog;

function NewRunRoutes() {
  const pathname = usePathname();
  return pathname === "/runs/new" ? <NewRun /> : <div>Run opened</div>;
}

beforeEach(async () => {
  vi.resetAllMocks();
  await i18n.changeLanguage("en");
  vi.mocked(api.capabilities).mockResolvedValue(capabilities);
  vi.mocked(api.providerModels).mockResolvedValue(modelCatalog);
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
  await screen.findAllByRole("option", { name: "Quick" });
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
    quick_reasoning_effort: "low",
    deep_reasoning_effort: "high",
    output_language: "zh-CN",
  });
  expect(vi.mocked(api.createRun).mock.calls[1][0]).not.toHaveProperty(
    "provenance",
  );
});

test("keeps UI locale and report output language independent", async () => {
  vi.mocked(api.createRun).mockResolvedValue({ id: "run-3" } as RunView);
  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );
  await screen.findAllByRole("option", { name: "Quick" });
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

test("shows a concise Simplified Chinese label", async () => {
  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );

  expect(
    await screen.findByRole("option", { name: "简体中文" }),
  ).toHaveValue("zh-CN");
  expect(
    screen.queryByRole("option", { name: /中国大陆/ }),
  ).not.toBeInTheDocument();
});

test("preserves a configured custom report language", async () => {
  const customLanguage = "Simplified Chinese (简体中文, zh-CN)";
  vi.mocked(api.capabilities).mockResolvedValue({
    ...capabilities,
    defaults: {
      ...capabilities.defaults,
      output_language: customLanguage,
    },
  });
  vi.mocked(api.createRun).mockResolvedValue({ id: "run-custom" } as RunView);

  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );
  await screen.findAllByRole("option", { name: "Quick" });

  const language = screen.getByLabelText(/^Report language/);
  expect(language).toHaveValue(customLanguage);
  expect(
    screen.getByRole("option", {
      name: `Configured default: ${customLanguage}`,
    }),
  ).toHaveValue(customLanguage);

  fireEvent.change(screen.getByLabelText(/^Ticker/), {
    target: { value: "NVDA" },
  });
  fireEvent.click(screen.getByRole("button", { name: /Queue research/ }));

  await waitFor(() => expect(api.createRun).toHaveBeenCalled());
  expect(vi.mocked(api.createRun).mock.calls[0][0].output_language).toBe(
    customLanguage,
  );
});

test("shows only configured providers and discovers models lazily", async () => {
  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );

  await screen.findAllByRole("option", { name: "Quick" });
  expect(api.providerModels).toHaveBeenCalledWith("openai");
  expect(screen.getByRole("option", { name: "OpenAI" })).toBeInTheDocument();
  expect(
    screen.queryByRole("option", { name: "Anthropic" }),
  ).not.toBeInTheDocument();
  expect(
    screen.getAllByRole("option", { name: "Custom model ID" }),
  ).toHaveLength(2);
});

test("unknown and custom model IDs only expose provider-default reasoning", async () => {
  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );
  await screen.findAllByRole("option", { name: "Future model" });

  fireEvent.change(screen.getByLabelText(/^Quick model/), {
    target: { value: "future-model" },
  });
  const quickReasoning = screen.getByLabelText(/^Quick reasoning/);
  expect(quickReasoning.querySelectorAll("option")).toHaveLength(1);

  fireEvent.change(screen.getByLabelText(/^Deep model/), {
    target: { value: "__custom_model_id__" },
  });
  expect(screen.getByLabelText(/^Deep reasoning/).querySelectorAll("option"))
    .toHaveLength(1);
  expect(
    screen.getByPlaceholderText("Custom model ID"),
  ).toBeInTheDocument();
});

test("keeps configured model IDs and custom entry when discovery is unavailable", async () => {
  vi.mocked(api.providerModels).mockRejectedValue(
    new Error("Catalog temporarily unavailable"),
  );
  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );

  await screen.findByText("Catalog temporarily unavailable");
  expect(screen.getByLabelText(/^Quick model/)).toHaveValue("gpt-5.4-mini");
  expect(screen.getByLabelText(/^Deep model/)).toHaveValue("gpt-5.5");
  expect(
    screen.getAllByRole("option", { name: "Custom model ID" }),
  ).toHaveLength(2);
  expect(screen.getByLabelText(/^Quick reasoning/)).toHaveValue(
    "provider_default",
  );
});

test("supports independent custom IDs for every selectable provider", async () => {
  vi.mocked(api.providerModels).mockImplementation(async (provider) =>
    provider === "openai"
      ? modelCatalog
      : {
          provider,
          models: [],
          source: "live",
          fetched_at: "2026-07-28T00:00:00Z",
          stale: false,
          warning: null,
        },
  );
  vi.mocked(api.createRun).mockResolvedValue({ id: "run-custom" } as RunView);
  render(
    <Router initialPath="/runs/new">
      <NewRunRoutes />
    </Router>,
  );
  await screen.findAllByRole("option", { name: "Quick" });

  fireEvent.change(screen.getByLabelText(/^Provider/), {
    target: { value: "ollama" },
  });
  await waitFor(() =>
    expect(api.providerModels).toHaveBeenCalledWith("ollama"),
  );
  const customInputs = await screen.findAllByPlaceholderText("Custom model ID");
  fireEvent.change(customInputs[0], { target: { value: "quick-local" } });
  fireEvent.change(customInputs[1], { target: { value: "deep-local" } });
  fireEvent.change(screen.getByLabelText(/^Ticker/), {
    target: { value: "NVDA" },
  });
  fireEvent.click(screen.getByRole("button", { name: /Queue research/ }));

  await waitFor(() => expect(api.createRun).toHaveBeenCalled());
  expect(vi.mocked(api.createRun).mock.calls[0][0]).toMatchObject({
    llm_provider: "ollama",
    quick_model: "quick-local",
    deep_model: "deep-local",
  });
});
