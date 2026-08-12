import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test } from "vitest";

import i18n from "../i18n";
import StatusBadge from "./StatusBadge";

describe("StatusBadge translations", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("zh-CN");
  });

  afterEach(async () => {
    await act(() => i18n.changeLanguage("zh-CN"));
  });

  test("uses the active UI locale independently of report language", async () => {
    const { rerender } = render(<StatusBadge status="running" />);
    expect(screen.getByText("运行中")).toBeVisible();

    await act(() => i18n.changeLanguage("ja"));
    rerender(<StatusBadge status="running" />);

    expect(screen.getByText("実行中")).toBeVisible();
  });

  test("renders every derived Review lifecycle as localized text, not color alone", async () => {
    const statuses = [
      "awaiting_observation",
      "observation_delayed",
      "awaiting_reflection",
      "reflection_retry_scheduled",
      "reflection_failed",
      "reflection_invalid",
      "feedback_available",
      "feedback_ineligible",
      "feedback_retired",
      "lifecycle_inconsistent",
    ];
    for (const language of ["en", "zh-CN", "ja"] as const) {
      await act(() => i18n.changeLanguage(language));
      const { unmount } = render(
        <>
          {statuses.map((status) => <StatusBadge key={status} status={status} />)}
        </>,
      );
      for (const status of statuses) {
        const label = i18n.t(`reviewLifecycle.${status}`);
        expect(label).not.toBe(`reviewLifecycle.${status}`);
        expect(screen.getByText(label)).toBeVisible();
      }
      unmount();
    }
  });
});
