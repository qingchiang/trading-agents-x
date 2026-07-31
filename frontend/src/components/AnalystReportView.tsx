import { useTranslation } from "react-i18next";

import type { AnalystReport } from "../api/client";
import type { EvidenceReferenceIndex } from "../evidence";
import Markdown from "./Markdown";

export default function AnalystReportView({
  report,
  evidenceIndex,
  onEvidence,
}: {
  report: AnalystReport | string;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
}) {
  const { t } = useTranslation();

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
  const claims = report.key_claims ?? [];

  return (
    <div className="analyst-report">
      <div className="report-audit-summary">
        {report.confidence !== null && report.confidence !== undefined && (
          <span>
            {t("confidence")} {Math.round(report.confidence * 100)}%
          </span>
        )}
        <span>
          {t("keyClaimsCount", { count: claims.length })}
        </span>
      </div>
      {report.audit_status === "incomplete" && (
        <div className="audit-incomplete-notice" role="status">
          {t("auditIncomplete")}
        </div>
      )}
      <Markdown
        evidenceAliases={evidenceIndex.aliases}
        onEvidence={onEvidence}
      >
        {report.markdown}
      </Markdown>
      {claims.length > 0 && (
        <details className="claim-audit-details">
          <summary>{t("keyClaimsAudit")}</summary>
          <ol>
            {claims.map((claim) => (
              <li key={claim.id}>
                <strong>{claim.statement}</strong>
                {claim.implication && <p>{claim.implication}</p>}
              </li>
            ))}
          </ol>
        </details>
      )}
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
