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
});
