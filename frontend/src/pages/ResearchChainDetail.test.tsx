import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import { api, type ResearchChain } from "../api/client";
import i18n from "../i18n";
import { Router } from "../router";
import ResearchChainDetail from "./ResearchChainDetail";

vi.mock("../api/client", () => ({
  api: { researchChain: vi.fn(), updateResearchChain: vi.fn() },
}));

const chain = {
  id: "chain-1",
  instrument: "6501.T",
  is_primary: true,
  current_revision_id: "revision-1",
  created_at: "2026-07-24T00:00:00Z",
  updated_at: "2026-07-24T00:00:00Z",
  revisions: [],
  current_revision: {
    id: "revision-1",
    chain_id: "chain-1",
    sequence: 1,
    producing_run_id: "run-1",
    cutoff: "2026-07-24",
    execution_strategy: "full",
    outcome: "material_change",
    created_at: "2026-07-24T00:00:00Z",
    metrics: { input_tokens: 1200, output_tokens: 300 },
    update_summary: { summary: "初回のフル分析を完了。" },
    current_state: {
      language: "ja",
      instrument: "6501.T",
      cutoff: "2026-07-24",
      opinion: {
        rating: "Hold",
        confidence: "medium",
        thesis: "需要证据持续确认利润率修复。",
      },
      claims: [
        { id: "claim-1", statement: "利润率正在修复。", confidence: "medium" },
      ],
      risks: [{ statement: "需求可能走弱。" }],
      catalysts: [{ statement: "订单增长。" }],
      invalidation_conditions: [{ statement: "利润率再次下降。" }],
      scenarios: [
        { kind: "base", likelihood: "medium", horizon: "12 months", outcome: "稳定" },
        { kind: "bull", likelihood: "low", horizon: "12 months", outcome: "改善" },
        { kind: "bear", likelihood: "low", horizon: "12 months", outcome: "恶化" },
      ],
      questions: [{ id: "question-1", question: "订单能否持续？" }],
    },
    coverage: {
      limitations: ["news archive limited"],
      domains: [{ domain: "market", status: "complete", limitations: [] }],
      claims: [{ object_id: "claim-1", status: "complete", limitations: [] }],
      questions: [{ object_id: "question-1", status: "limited", limitations: ["answer pending"] }],
    },
    evidence_snapshot: {
      bundle: {
        items: [
          {
            ref: "ev_0123456789ab",
            source: "TDnet",
            evidence_type: "announcement",
            content: "订单同比增长。",
          },
        ],
      },
      lineage: [{ evidence_ref: "ev_0123456789ab", lineage: "new" }],
    },
  },
} as unknown as ResearchChain;
chain.revisions = [chain.current_revision!];

beforeEach(async () => {
  vi.resetAllMocks();
  await i18n.changeLanguage("en");
  vi.mocked(api.researchChain).mockResolvedValue(chain);
});

test("reads the complete current thesis, coverage, evidence, reports, and metrics", async () => {
  render(
    <Router initialPath="/research/chain-1">
      <ResearchChainDetail />
    </Router>,
  );

  expect(await screen.findByText("需要证据持续确认利润率修复。")).toBeVisible();
  expect(screen.getByText("初回のフル分析を完了。")).toBeVisible();
  expect(screen.getByText("订单能否持续？")).toBeVisible();
  expect(screen.getByText("利润率再次下降。")).toBeVisible();
  expect(screen.getByText("news archive limited")).toBeVisible();
  expect(screen.getByText("answer pending")).toBeVisible();
  expect(screen.getByText("ev_0123456789ab")).toBeVisible();
  expect(screen.getByText("订单同比增长。")).toBeVisible();
  expect(screen.getByRole("link", { name: "Full analysis" })).toHaveAttribute(
    "href",
    "/runs/run-1",
  );
  expect(screen.getByText(/Input tokens: 1,200/)).toBeVisible();
  expect(screen.getByRole("link", { name: "Revision export" })).toHaveAttribute(
    "href",
    "/api/v1/research-revisions/revision-1/export?format=json",
  );
  expect(
    screen.getByRole("link", { name: "Producing run and Full artifacts" }),
  ).toHaveAttribute("href", "/runs/run-1");
});

test("queues a Full update from the displayed current head", async () => {
  vi.mocked(api.updateResearchChain).mockResolvedValue({ id: "run-2" } as never);
  render(
    <Router initialPath="/research/chain-1">
      <ResearchChainDetail />
    </Router>,
  );

  fireEvent.click(await screen.findByRole("button", { name: "Update with Full Analysis" }));

  await waitFor(() => expect(api.updateResearchChain).toHaveBeenCalled());
  expect(vi.mocked(api.updateResearchChain).mock.calls[0].slice(0, 2)).toEqual([
    "chain-1",
    { baseline_revision_id: "revision-1", analysis_date: "2026-07-25" },
  ]);
});
