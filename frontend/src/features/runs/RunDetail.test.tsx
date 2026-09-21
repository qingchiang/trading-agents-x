import { render, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";
import { Router } from "../../app/router";
import { useRunRecord } from "../../shared/useRunRecord";
import i18n from "../../shared/i18n";
import RunDetail from "./RunDetail";

vi.mock("../../shared/useRunRecord", () => ({ useRunRecord: vi.fn() }));
vi.mock("./RunExecutionView", () => ({default: ({diagnostics}: {diagnostics: boolean}) => <div>{diagnostics ? "Diagnostic content" : "Execution content"}</div>}));

test.each(["decision", "reports", "brief", "incremental", "evidence"])("retired reading query %s stays on the execution page", async view => {
  await i18n.changeLanguage("en");
  vi.mocked(useRunRecord).mockReturnValue({detail: {run: {id: "retained", is_research_node: true}}} as ReturnType<typeof useRunRecord>);
  render(<Router initialPath={`/runs/retained?view=${view}`}><RunDetail /></Router>);
  expect(await screen.findByText("Execution content")).toBeVisible();
  expect(useRunRecord).toHaveBeenLastCalledWith("retained", "timeline");
});

test("loads diagnostics only for the explicit diagnostic view", async () => {
  vi.mocked(useRunRecord).mockReturnValue({detail: {run: {id: "retained"}}} as ReturnType<typeof useRunRecord>);
  render(<Router initialPath="/runs/retained?view=diagnostics"><RunDetail /></Router>);
  expect(await screen.findByText("Diagnostic content")).toBeVisible();
  expect(useRunRecord).toHaveBeenLastCalledWith("retained", "diagnostics");
});
