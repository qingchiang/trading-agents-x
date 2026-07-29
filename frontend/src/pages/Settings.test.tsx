import { render, screen } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { api, type Capabilities } from "../api/client";
import i18n from "../i18n";
import Settings from "./Settings";

vi.mock("../api/client", () => ({
  api: {
    capabilities: vi.fn(),
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
    quick_reasoning_effort: null,
    deep_reasoning_effort: null,
    output_language: "zh-CN",
    lan_enabled: false,
    trash_retention_days: 30,
  },
} as Capabilities;

beforeEach(async () => {
  vi.resetAllMocks();
  await i18n.changeLanguage("en");
  vi.mocked(api.capabilities).mockResolvedValue(capabilities);
});

test("shows configured and unavailable providers without exposing secrets", async () => {
  render(<Settings />);

  expect(await screen.findByText("OpenAI")).toBeInTheDocument();
  expect(screen.getByText("Anthropic")).toBeInTheDocument();
  expect(screen.getByText("Ollama")).toBeInTheDocument();
  expect(screen.getAllByText("openai")).toHaveLength(2);
  expect(screen.getAllByText("Configured")).toHaveLength(1);
  expect(screen.getAllByText("Missing")).toHaveLength(1);
  expect(screen.getAllByText("Ready")).toHaveLength(1);
  expect(screen.queryByText("private-key")).not.toBeInTheDocument();
});
