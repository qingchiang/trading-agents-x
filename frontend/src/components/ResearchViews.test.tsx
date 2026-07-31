import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import type {
  AnalystReport,
  EvidenceBundle,
  ResearchArtifact,
} from "../api/client";
import { buildEvidenceReferenceIndex } from "../evidence";
import i18n from "../i18n";
import AnalystReportView from "./AnalystReportView";
import DeliberationView from "./DeliberationView";
import EvidenceTableView from "./EvidenceTableView";

const evidenceRef = "ev_0123456789ab";
const evidence: EvidenceBundle = {
  version: "5",
  instrument: "NVDA",
  analysis_date: "2026-07-24",
  digest: "fixture",
  items: [
    {
      ref: evidenceRef,
      source: "fixture",
      evidence_type: "market",
      requested_date: "2026-07-24",
      content: "The sealed source body.",
      quality: "high",
      fallback: false,
    },
  ],
  tables: [
    {
      id: "et_market",
      title: "Daily prices",
      purpose: "Raw price audit",
      source_format: "structured",
      evidence_refs: [evidenceRef],
      columns: [
        { key: "date", label: "Date", data_type: "date" },
        { key: "close", label: "Close", data_type: "number", unit: "USD" },
      ],
      rows: [
        {
          id: "row.1",
          cells: {
            date: { raw_value: "2026-07-24" },
            close: { raw_value: 100 },
          },
        },
      ],
    },
  ],
};
const evidenceIndex = buildEvidenceReferenceIndex(evidence);
const report: AnalystReport = {
  analyst: "market",
  markdown: [
    "# Market view",
    "",
    "The trend is constructive.[^ev_0123456789ab]",
    "",
    "| 指标 | 数值 |",
    "| --- | ---: |",
    "| 收盘价 | 100 美元 |",
  ].join("\n"),
  report_sections: [
    {
      id: "market-view",
      title: "Market view",
      anchor: "market-view",
      source_refs: [evidenceRef],
    },
  ],
  confidence: 0.72,
  key_claims: [
    {
      id: "market.claim.1",
      section_id: "market-view",
      kind: "inference",
      importance: "primary",
      statement: "The trend is constructive.",
      implication: "Upside remains possible.",
      confidence: 0.72,
      evidence_refs: [evidenceRef],
    },
  ],
  source_refs: [evidenceRef],
  audit_status: "complete",
  warnings: [],
};

beforeEach(async () => {
  await i18n.changeLanguage("en");
});

test("renders natural Markdown tables and unobtrusive evidence footnotes", () => {
  const onEvidence = vi.fn();
  render(
    <AnalystReportView
      report={report}
      runId="run-1"
      reportKey="market"
      evidenceIndex={evidenceIndex}
      onEvidence={onEvidence}
    />,
  );

  expect(screen.getByRole("table")).toHaveTextContent("收盘价100 美元");
  expect(
    screen.getByRole("navigation", { name: "Report section navigation" }),
  ).toBeVisible();
  expect(screen.getByRole("heading", { name: "Market view" })).toHaveAttribute(
    "id",
    "user-content-market-view",
  );
  expect(screen.queryByText(evidenceRef)).not.toBeInTheDocument();
  const footnote = screen.getByRole("button", {
    name: `Open evidence ${evidenceRef}`,
  });
  expect(footnote).toHaveTextContent("E01");
  fireEvent.click(footnote);
  expect(onEvidence).toHaveBeenCalledWith(evidenceRef);
});

test("keeps a readable report when automated audit extraction is incomplete", () => {
  render(
    <AnalystReportView
      report={{
        ...report,
        audit_status: "incomplete",
        key_claims: [],
      }}
      runId="run-1"
      reportKey="market"
      evidenceIndex={evidenceIndex}
      onEvidence={vi.fn()}
    />,
  );

  expect(screen.getByRole("heading", { name: "Market view" })).toBeVisible();
  expect(
    screen.getByText(
      "The report is available, but automated claim extraction is incomplete.",
    ),
  ).toBeVisible();
});

test("shows raw evidence tables outside the reading report", () => {
  render(
    <EvidenceTableView
      table={evidence.tables?.[0] as NonNullable<EvidenceBundle["tables"]>[number]}
      evidenceIndex={evidenceIndex}
      onEvidence={vi.fn()}
    />,
  );
  expect(screen.getByRole("heading", { name: "Daily prices" })).toBeVisible();
  expect(screen.getByRole("table")).toHaveTextContent("2026-07-24100");
});

test("organizes shallow Markdown deliberation by role and issue", () => {
  const base = {
    run_id: "run-1",
    attempt: 1,
    schema_version: "2",
    prompt_version: "research-v3",
    generation_method: "markdown_audited" as const,
    created_at: "2026-07-24T00:00:00Z",
  };
  const artifacts = [
    {
      ...base,
      id: "bull",
      stage: "case",
      role: "bull",
      round: 0,
      content: {
        role: "bull",
        markdown: `Bull thesis.[^${evidenceRef}]`,
      },
    },
    {
      ...base,
      id: "bear",
      stage: "case",
      role: "bear",
      round: 0,
      content: {
        role: "bear",
        markdown: "Bear thesis.",
      },
    },
    {
      ...base,
      id: "agenda",
      stage: "agenda",
      role: "moderator",
      round: 0,
      content: {
        summary: "Material issues",
        issues: [
          {
            id: "issue.1",
            question: "Is demand durable?",
            importance: "material",
          },
        ],
      },
    },
    {
      ...base,
      id: "judge",
      stage: "judge",
      role: "research_judge",
      round: 0,
      content: {
        markdown: "Evidence remains mixed.",
        preliminary_rating: "Hold",
        confidence: 0.61,
        issue_dispositions: [
          { issue_id: "issue.1", status: "unresolved" },
        ],
      },
    },
  ] as ResearchArtifact[];

  render(
    <DeliberationView
      artifacts={artifacts}
      evidenceIndex={evidenceIndex}
      onEvidence={vi.fn()}
    />,
  );

  expect(screen.getByRole("heading", { name: "Bull and bear cases" })).toBeVisible();
  expect(screen.getByText("Bull thesis.")).toBeVisible();
  expect(screen.getByText("Bear thesis.")).toBeVisible();
  expect(screen.getByText("Is demand durable?")).toBeVisible();
  expect(screen.getByText(/material · unresolved/)).toBeVisible();
  expect(screen.getByText("Evidence remains mixed.")).toBeVisible();
});
