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
    research_update_audit: {
      mode: "shadow",
      candidate: {
        outcome: "no_material_change",
        coverage: {},
        update_summary: { summary: "No bounded material change detected." },
        evidence_snapshot: {},
      },
      coverage: {
        supports_no_material_change: true,
        limitations: ["Bounded archive constraint"],
        domains: [{ domain: "company_disclosures", status: "complete" }],
      },
      authoritative_strategy: "full",
      escalation_reason: null,
      comparison: "disagreement",
      checked_windows: [{ source: "EDINET", scanned_start: "2026-07-01", scanned_end: "2026-07-25", status: "complete" }],
      evidence_lineage: [{ evidence_ref: "ev_0123456789ab", lineage: "new" }],
      semantic_assessment: {
        schema_version: "1",
        language: "ja",
        summary: "新しい証拠は既存の主張を支持します。",
        relationships: [{
          evidence_refs: ["ev_0123456789ab"],
          relationship: "support",
          suggested_claim_ids: ["claim-1"],
          suggested_question_ids: [],
        }],
      },
      bounded_metrics: { llm_calls: 0, tool_calls: 3, input_tokens: 0, output_tokens: 0, wall_time_seconds: 0.4 },
      full_metrics: { llm_calls: 8, tool_calls: 12, input_tokens: 1200, output_tokens: 300, wall_time_seconds: 4.2 },
    },
    update_summary: { summary: "初回のフル分析を完了。" },
    delta: {
      claims: [],
      questions: [],
      change_signals: [
        {
          kind: "market_boundary_crossing",
          domain: "market",
          record_id: "jquants-market:6501.T",
          previous_version_id: "market:v1",
          current_version_id: "market:v2",
          requires_full_analysis: true,
          detail: "The observed market value crossed a thesis-relevant reference.",
          boundary_label: "Thesis reference",
          boundary_value: 100,
          previous_value: 95,
          current_value: 101,
        },
      ],
    },
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
      supports_no_material_change: false,
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
      source_records: [
        {
          source: "EDINET",
          record_id: "S100ROOT",
          version_id: "edinet:S100CORRECTION",
          status: "corrected",
          published_at: "2026-07-23 15:00",
          available_at: "2026-07-23T15:00:00+09:00",
          availability_basis: "source disclosure timestamp",
          title: "訂正有価証券報告書",
          evidence_ref: "ev_0123456789ab",
          replaces_version_id: "edinet:S100ROOT",
          native_record_id: "S100CORRECTION",
          adjustment: "J-Quants adjusted OHLCV v2",
          unit: "JPY",
          precision: 2,
          fallback: true,
        },
      ],
      source_record_lineage: [
        {
          version_id: "edinet:S100CORRECTION",
          lineage: "new",
          observed_in_execution: true,
        },
      ],
      source_watermarks: [
        {
          source: "TDnet",
          scanned_start: "2026-06-24",
          scanned_end: "2026-07-24",
          status: "limited",
          limitations: ["rolling archive truncated the requested interval"],
          returned_records: 2,
          reported_records: 5,
        },
      ],
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
  expect(screen.getByText("訂正有価証券報告書")).toBeVisible();
  expect(screen.getByText(/edinet:S100CORRECTION/)).toBeVisible();
  expect(screen.getByText(/native S100CORRECTION/)).toBeVisible();
  expect(screen.getByText(/source disclosure timestamp/)).toBeVisible();
  expect(screen.getByText(/JPY\/2/)).toBeVisible();
  expect(screen.getByText(/fallback true/)).toBeVisible();
  expect(screen.getByText(/2026-06-24.*2026-07-24/)).toBeVisible();
  expect(screen.getByText("Shadow update finding")).toBeVisible();
  expect(screen.getByText(/Candidate outcome: no_material_change/)).toBeVisible();
  expect(screen.getByText(/Shadow comparison: disagreement/)).toBeVisible();
  expect(screen.getByText(/Bounded checked windows: EDINET 2026-07-01–2026-07-25/)).toBeVisible();
  expect(screen.getByText(/Bounded update summary: No bounded material change detected/)).toBeVisible();
  expect(screen.getByText(/Bounded coverage attestation: true; company_disclosures \(complete\)/)).toBeVisible();
  expect(screen.getByText("Bounded archive constraint")).toBeVisible();
  expect(screen.getByText(/Bounded Evidence lineage: ev_0123456789ab \(new\)/)).toBeVisible();
  expect(screen.getByText(/Semantic assessment: 新しい証拠は既存の主張を支持します。/)).toBeVisible();
  expect(screen.getByText(/support.*claim-1/)).toBeVisible();
  expect(screen.getByText(/Bounded work: 0 LLM calls · 3 Tool calls/)).toBeVisible();
  expect(screen.getByText("rolling archive truncated the requested interval")).toBeVisible();
  expect(screen.getByText("market_boundary_crossing")).toBeVisible();
  expect(screen.getByText(/Thesis reference: 100/)).toBeVisible();
  expect(screen.getByText(/95 → 101/)).toBeVisible();
  expect(screen.getByText(/requires Full Analysis/)).toBeVisible();
  expect(screen.getByText("Quiet reassessment blocked")).toBeVisible();
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

test("queues a Shadow update from the displayed current head", async () => {
  vi.mocked(api.updateResearchChain).mockResolvedValue({ id: "run-2" } as never);
  render(
    <Router initialPath="/research/chain-1">
      <ResearchChainDetail />
    </Router>,
  );

  fireEvent.click(await screen.findByRole("button", { name: "Update research (Shadow)" }));

  await waitFor(() => expect(api.updateResearchChain).toHaveBeenCalled());
  expect(vi.mocked(api.updateResearchChain).mock.calls[0].slice(0, 2)).toEqual([
    "chain-1",
    { baseline_revision_id: "revision-1", analysis_date: "2026-07-25" },
  ]);
});
