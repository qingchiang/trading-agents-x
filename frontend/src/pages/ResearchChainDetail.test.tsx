import { render, screen } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import { api, type ResearchChain } from "../api/client";
import i18n from "../i18n";
import { Router } from "../router";
import ResearchChainDetail from "./ResearchChainDetail";

vi.mock("../api/client", () => ({
  api: { researchChain: vi.fn() },
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
    coverage: { limitations: ["news archive limited"] },
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
  expect(screen.getByText("ev_0123456789ab")).toBeVisible();
  expect(screen.getByText("订单同比增长。")).toBeVisible();
  expect(screen.getByRole("link", { name: "Full analysis" })).toHaveAttribute(
    "href",
    "/runs/run-1",
  );
  expect(screen.getByText(/Input tokens: 1,200/)).toBeVisible();
});
