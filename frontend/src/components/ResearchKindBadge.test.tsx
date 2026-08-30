import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, test } from "vitest";

import i18n from "../i18n";
import ResearchKindBadge from "./ResearchKindBadge";

beforeEach(async () => {
  await i18n.changeLanguage("en");
});

test("pins a Full research configuration and closes it accessibly", () => {
  render(
    <ResearchKindBadge
      kind="full"
      request={{
        profile: "standard",
        analysts: ["market", "news"],
        llm_provider: "openai",
        quick_model: "quick-model",
        deep_model: "deep-model",
        quick_reasoning_effort: "low",
        deep_reasoning_effort: "high",
      }}
    />,
  );

  const trigger = screen.getByRole("button", { name: "Full research" });
  fireEvent.focus(trigger);
  const tooltip = screen.getByRole("tooltip");
  expect(trigger).toHaveAttribute("aria-describedby", tooltip.id);
  expect(tooltip).toHaveTextContent("Standard");
  expect(tooltip).toHaveTextContent("Market, News");
  expect(tooltip).toHaveTextContent("quick-model");
  expect(tooltip).toHaveTextContent("deep-model");

  fireEvent.click(trigger);
  expect(trigger.closest(".research-kind-tooltip-root")).toHaveClass("pinned");
  expect(trigger).toHaveAttribute("aria-expanded", "true");

  fireEvent.keyDown(document, { key: "Escape" });
  expect(trigger.closest(".research-kind-tooltip-root")).not.toHaveClass("pinned");
  expect(trigger).toHaveAttribute("aria-expanded", "false");
});

test("closes a focus-opened configuration with Escape", () => {
  render(<ResearchKindBadge kind="full" />);

  const trigger = screen.getByRole("button", { name: "Full research" });
  fireEvent.focus(trigger);
  expect(screen.getByRole("tooltip")).toBeVisible();

  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
});

test("shows only the deep model path for Incremental research", () => {
  render(
    <ResearchKindBadge
      kind="incremental"
      request={{
        profile: "standard",
        analysts: ["market", "news", "fundamentals"],
        llm_provider: "deepseek",
        quick_model: "compatibility-quick-model",
        deep_model: "deep-model",
        quick_reasoning_effort: "low",
        deep_reasoning_effort: "high",
      }}
    />,
  );

  fireEvent.mouseEnter(
    screen.getByRole("button", { name: "Incremental research" }),
  );
  const tooltip = screen.getByRole("tooltip");
  expect(tooltip).toHaveTextContent("3 information domains");
  expect(tooltip).toHaveTextContent("Market, News, Fundamentals");
  expect(tooltip).toHaveTextContent("deep-model");
  expect(tooltip).not.toHaveTextContent("compatibility-quick-model");
  expect(tooltip).not.toHaveTextContent("Quick model");
  expect(tooltip).not.toHaveTextContent("Standard");
});

test("uses retained method snapshot fields without inventing a profile", () => {
  render(
    <ResearchKindBadge
      kind="full"
      methodSnapshot={{
        enabled_roles: ["market"],
        llm_provider: "fixture-provider",
        quick_model: "fixture-quick",
        deep_model: "fixture-deep",
        quick_reasoning_effort: "provider_default",
        deep_reasoning_effort: "medium",
      }}
    />,
  );

  fireEvent.focus(screen.getByRole("button", { name: "Full research" }));
  const tooltip = screen.getByRole("tooltip");
  expect(tooltip).toHaveTextContent("fixture-provider");
  expect(tooltip).toHaveTextContent("fixture-quick");
  expect(tooltip).toHaveTextContent("fixture-deep");
  expect(tooltip).not.toHaveTextContent("Standard");
});
