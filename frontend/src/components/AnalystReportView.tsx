import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import type {
  AnalystReport,
  EvidenceBundle,
  EvidenceTable,
  ResearchTable,
} from "../api/client";
import type { EvidenceReferenceIndex } from "../evidence";
import EvidenceLinks from "./EvidenceLinks";
import Markdown from "./Markdown";
import ResearchTableView from "./ResearchTableView";

export default function AnalystReportView({
  report,
  evidence,
  evidenceIndex,
  onEvidence,
}: {
  report: AnalystReport | string;
  evidence: EvidenceBundle | null;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
}) {
  const { t } = useTranslation();
  const evidenceTables = useMemo(
    () => new Map((evidence?.tables ?? []).map((table) => [table.id, table])),
    [evidence?.tables],
  );

  if (typeof report === "string") {
    return (
      <Markdown
        evidenceAliases={evidenceIndex.aliases}
        onEvidence={onEvidence}
      >
        {report}
      </Markdown>
    );
  }

  const researchTables = new Map(
    (report.tables ?? []).map((table) => [table.id, table]),
  );

  return (
    <div className="analyst-report">
      <section className="report-executive-summary">
        <div className="report-section-heading">
          <h3>{t("executiveSummary")}</h3>
          <span>
            {t("confidence")} {Math.round(report.confidence * 100)}%
          </span>
        </div>
        <Markdown
          evidenceAliases={evidenceIndex.aliases}
          onEvidence={onEvidence}
        >
          {report.executive_summary}
        </Markdown>
      </section>

      {report.sections.map((section) => (
        <section className="analyst-report-section" key={section.id}>
          <h3>{section.title}</h3>
          <Markdown
            evidenceAliases={evidenceIndex.aliases}
            onEvidence={onEvidence}
          >
            {section.narrative}
          </Markdown>
          {section.table_ids?.map((tableId) => {
            const evidenceTable = evidenceTables.get(tableId);
            const researchTable = researchTables.get(tableId);
            const table = evidenceTable ?? researchTable;
            if (!table) {
              return (
                <div className="missing-table" key={tableId}>
                  {t("missingResearchTable", { id: tableId })}
                </div>
              );
            }
            return (
              <ResearchTableView
                table={table}
                sourceTable={sourceTableFor(table, evidenceTables)}
                evidenceIndex={evidenceIndex}
                onEvidence={onEvidence}
                key={tableId}
              />
            );
          })}
        </section>
      ))}

      <section className="analyst-claims">
        <h3>{t("analystClaims")}</h3>
        <div className="claim-list">
          {report.claims.map((claim) => (
            <article className="claim-card" key={claim.id}>
              <header>
                <code>{claim.id}</code>
                <span className={`claim-kind claim-kind-${claim.kind}`}>
                  {t(claimTypeKey(claim.kind))}
                </span>
                <span>
                  {t("confidence")} {Math.round(claim.confidence * 100)}%
                </span>
              </header>
              <Markdown
                evidenceAliases={evidenceIndex.aliases}
                onEvidence={onEvidence}
              >
                {claim.statement}
              </Markdown>
              <div className="claim-implication">
                <strong>{t("implication")}</strong>
                <Markdown
                  evidenceAliases={evidenceIndex.aliases}
                  onEvidence={onEvidence}
                >
                  {claim.implication}
                </Markdown>
              </div>
              <EvidenceLinks
                refs={claim.evidence_refs}
                evidenceIndex={evidenceIndex}
                onEvidence={onEvidence}
                compact
              />
            </article>
          ))}
        </div>
      </section>

      <div className="report-conclusion-grid">
        <MarkdownList
          title={t("catalysts")}
          items={report.catalysts ?? []}
          empty={t("noCatalystsIdentified")}
          evidenceIndex={evidenceIndex}
          onEvidence={onEvidence}
        />
        <MarkdownList
          title={t("risks")}
          items={report.risks}
          evidenceIndex={evidenceIndex}
          onEvidence={onEvidence}
        />
        <MarkdownList
          title={t("invalidation")}
          items={report.invalidation_conditions}
          evidenceIndex={evidenceIndex}
          onEvidence={onEvidence}
        />
      </div>
    </div>
  );
}

export function MarkdownList({
  title,
  items,
  empty = "—",
  evidenceIndex,
  onEvidence,
}: {
  title: string;
  items: string[];
  empty?: string;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
}) {
  return (
    <section className="research-list">
      <h3>{title}</h3>
      {items.length > 0 ? (
        <ul>
          {items.map((item, index) => (
            <li key={`${index}:${item}`}>
              <Markdown
                evidenceAliases={evidenceIndex.aliases}
                onEvidence={onEvidence}
              >
                {item}
              </Markdown>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">{empty}</p>
      )}
    </section>
  );
}

function sourceTableFor(
  table: EvidenceTable | ResearchTable,
  evidenceTables: Map<string, EvidenceTable>,
): EvidenceTable | undefined {
  if ("source_table_id" in table && table.source_table_id) {
    return evidenceTables.get(table.source_table_id);
  }
  return undefined;
}

function claimTypeKey(
  kind: AnalystReport["claims"][number]["kind"],
): string {
  const labels = {
    observation: "claimObservation",
    inference: "claimInference",
    forecast: "claimForecast",
  } as const;
  return labels[kind];
}
