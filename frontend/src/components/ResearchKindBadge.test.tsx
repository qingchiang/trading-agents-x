import { render, screen } from "@testing-library/react";
import { expect, test } from "vitest";
import i18n from "../i18n";
import ResearchKindBadge from "./ResearchKindBadge";

test("shows research kind without exposing method snapshots in reading", async () => {
  await i18n.changeLanguage("en");
  render(<ResearchKindBadge kind="incremental" methodSnapshot={{ deep_model: "private-model", schema_version: "2" }} />);
  expect(screen.getByText("Incremental research")).toBeVisible();
  expect(screen.queryByRole("button")).toBeNull();
  expect(screen.queryByText("private-model")).toBeNull();
});
