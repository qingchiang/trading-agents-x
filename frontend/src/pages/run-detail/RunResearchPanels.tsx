import { useEffect, useState } from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import {
  type AnalysisResult,
  type AnalystReport,
  type ResearchArtifact,
  type ResearchDecision,
  type RunDetail as RunDetailType,
  type StructuredRecoveryNotice,
} from "../../api/client";
import AnalystReportView from "../../components/AnalystReportView";
import ResearchDecisionView from "../../components/ResearchDecisionView";
import {
  groupEvidenceRefs,
  type EvidenceDisplayGroup,
  type EvidenceReferenceIndex,
} from "../../evidence";
import { formatUtcDate, trashDeadline } from "../../trash";

const reportOrder = ["fundamentals", "market", "news", "social"] as const;
const viewNames = [
  "timeline",
  "deliberation",
  "evidence",
  "reports",
  "decision",
] as const;
type ViewName = (typeof viewNames)[number];
type ReturnViewName = Exclude<ViewName, "evidence">;
type ArtifactContent = ResearchArtifact["content"];
type VisibleWarning =
  | string
  | NonNullable<AnalystReport["warnings"]>[number];

export function ReportsPanel({
  runId,
  reports,
  reportNames,
  activeReport,
  onReport,
  onEvidence,
  evidenceIndex,
}: {
  runId: string;
  reports: Record<string, AnalystReport | string>;
  reportNames: string[];
  activeReport: string;
  onReport: (report: string) => void;
  onEvidence: (ref: string) => void;
  evidenceIndex: EvidenceReferenceIndex;
}) {
  const { t } = useTranslation();
  return (
    <article
      className="panel audit-panel report-panel reader-panel"
      id="run-view-reports"
      role="tabpanel"
    >
      <div className="panel-header">
        <div>
          <p className="eyebrow">{t("researchArtifacts")}</p>
          <h2>{t("reports")}</h2>
        </div>
      </div>
      {reportNames.length === 0 ? (
        <div className="empty-state">{t("noReports")}</div>
      ) : (
        <>
          <div className="tabs">
            {reportNames.map((name) => (
              <button
                className={activeReport === name ? "active" : ""}
                onClick={() => onReport(name)}
                key={name}
              >
                {reportLabel(t, name)}
              </button>
            ))}
          </div>
          <AnalystReportView
            report={reports[activeReport]}
            runId={runId}
            reportKey={activeReport}
            evidenceIndex={evidenceIndex}
            onEvidence={onEvidence}
          />
          <ReportMetadata
            report={reports[activeReport]}
            onEvidence={onEvidence}
            evidenceIndex={evidenceIndex}
            key={activeReport}
          />
        </>
      )}
    </article>
  );
}

export function DecisionPanel({
  decision,
  numericAudit,
  onEvidence,
  evidenceIndex,
  onOpenWarnings,
}: {
  decision: ResearchDecision | null;
  numericAudit: AnalysisResult["numeric_audit"];
  onEvidence: (ref: string) => void;
  evidenceIndex: EvidenceReferenceIndex;
  onOpenWarnings: () => void;
}) {
  return (
    <ResearchDecisionView
      decision={decision}
      numericAudit={numericAudit}
      onEvidence={onEvidence}
      evidenceIndex={evidenceIndex}
      onOpenWarnings={onOpenWarnings}
    />
  );
}

function ReportMetadata({
  report,
  onEvidence,
  evidenceIndex,
}: {
  report: unknown;
  onEvidence: (ref: string) => void;
  evidenceIndex: EvidenceReferenceIndex;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (!report || typeof report !== "object") return null;
  const typed = report as {
    warnings?: VisibleWarning[];
    source_refs?: string[];
  };
  const warnings = dedupeWarnings(typed.warnings ?? []);
  const refs = typed.source_refs ?? [];
  const groups = groupEvidenceRefs(refs, evidenceIndex)
    .map((refGroup) =>
      evidenceIndex.groups.find(
        (group) => group.alias === refGroup.alias,
      ),
    )
    .filter((group): group is EvidenceDisplayGroup => Boolean(group));
  if (!warnings.length && !groups.length) return null;
  return (
    <details
      className="report-metadata audit-details"
      open={open}
    >
      <summary
        onClick={(event) => {
          event.preventDefault();
          const next = !open;
          setOpen(next);
        }}
      >
        <strong>{t("auditDetails")}</strong>
        <span className="details-summary-meta">
          <span>
            {t("auditSummary", {
              warnings: warnings.length,
              evidence: groups.length,
            })}
          </span>
          <span className="details-chevron" aria-hidden="true" />
        </span>
      </summary>
      <div className="audit-details-body">
        {warnings.length > 0 && (
          <div>
            <strong>{t("warnings")}</strong>
            <ul>
              {warnings.map((warning) => (
                <li key={warningKey(warning)}>
                  {warningMessage(warning)}
                </li>
              ))}
            </ul>
          </div>
        )}
        {groups.length > 0 && (
          <div>
            <strong>{t("evidenceRefs")}</strong>
            <ul className="audit-evidence-grid">
              {groups.map((group) => (
                <li key={group.alias}>
                  <button
                    type="button"
                    className="open-evidence-button"
                    onClick={() => onEvidence(group.canonical.ref)}
                    title={group.refs.join("\n")}
                    aria-label={`${t("openEvidence")} ${group.canonical.ref}`}
                  >
                    {group.alias}
                  </button>
                  <span className="audit-source-name">
                    {group.sources.join(", ")}
                  </span>
                  <span className={`quality quality-${group.quality}`}>
                    {group.quality}
                  </span>
                  {group.fallback && (
                    <span className="audit-fallback-chip">{t("fallback")}</span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </details>
  );
}

export function RunWarnings({
  warnings,
  openRequest,
}: {
  warnings: VisibleWarning[];
  openRequest: number;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (openRequest === 0) return;
    setOpen(true);
    requestAnimationFrame(() => {
      document.getElementById("run-warnings")?.scrollIntoView?.({
        behavior: "smooth",
        block: "nearest",
      });
    });
  }, [openRequest]);
  if (!warnings.length) return null;
  return (
    <details
      id="run-warnings"
      className="run-warning-details audit-details"
      open={open}
    >
      <summary
        onClick={(event) => {
          event.preventDefault();
          const next = !open;
          setOpen(next);
        }}
      >
        <strong>{t("runWarnings")}</strong>
        <span>
          {t("warningCount", { count: warnings.length })}
        </span>
      </summary>
      <ul>
        {warnings.map((warning) => (
          <li key={warningKey(warning)}>{warningMessage(warning)}</li>
        ))}
      </ul>
    </details>
  );
}

export function RecoveryNotices({
  notices,
}: {
  notices: StructuredRecoveryNotice[];
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (notices.length === 0) return null;
  return (
    <details className="run-recovery-details audit-details" open={open}>
      <summary
        onClick={(event) => {
          event.preventDefault();
          setOpen(!open);
        }}
      >
        <strong>{t("structuredRecoveries")}</strong>
        <span>{t("recoveryCount", { count: notices.length })}</span>
      </summary>
      <div className="recovery-notice-list">
        {notices.map((notice, index) => (
          <article key={`${notice.attempt}:${notice.node}:${index}`}>
            <strong>{notice.node}</strong>
            <span>
              {t("recoveryNoticeSummary", {
                reason: notice.initial_reason_code,
                method: notice.recovery_method,
                calls: notice.retry_count,
              })}
            </span>
            {(notice.validation_issue_codes ?? []).length > 0 && (
              <code>{(notice.validation_issue_codes ?? []).join(", ")}</code>
            )}
          </article>
        ))}
      </div>
    </details>
  );
}

export function reportWarnings(report: AnalystReport | string): VisibleWarning[] {
  return typeof report === "string"
    ? []
    : [...(report.warnings ?? [])];
}

function warningMessage(warning: VisibleWarning): string {
  return typeof warning === "string" ? warning : warning.message;
}

export function warningKey(warning: VisibleWarning): string {
  return typeof warning === "string"
    ? warning
    : [
        warning.code,
        warning.evidence_ref,
        warning.source,
        warning.message,
      ].join(":");
}

export function dedupeWarnings(warnings: VisibleWarning[]): VisibleWarning[] {
  const seen = new Set<string>();
  return warnings.filter((warning) => {
    const key = warningKey(warning);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
export function isAnalystReport(content: ArtifactContent): content is AnalystReport {
  return (
    "analyst" in content &&
    "markdown" in content &&
    "report_sections" in content &&
    "audit_status" in content
  );
}

function isResearchDecision(
  content: ArtifactContent,
): content is ResearchDecision {
  return (
    "rating" in content &&
    "thesis" in content &&
    "scenarios" in content
  );
}

export function latestResearchDecision(
  artifacts: ResearchArtifact[],
): ResearchDecision | null {
  for (const artifact of [...artifacts].reverse()) {
    if (
      artifact.stage === "decision" &&
      isResearchDecision(artifact.content)
    ) {
      return artifact.content;
    }
  }
  return null;
}

export function isViewName(value: string | null): value is ViewName {
  return value !== null && (viewNames as readonly string[]).includes(value);
}

export function isReturnViewName(value: string | null): value is ReturnViewName {
  return isViewName(value) && value !== "evidence";
}

export function orderReportNames(names: string[]): string[] {
  const known = reportOrder.filter((name) => names.includes(name));
  const extensions = names
    .filter((name) => !(reportOrder as readonly string[]).includes(name))
    .sort();
  return [...known, ...extensions];
}

function reportLabel(t: TFunction, name: string): string {
  const labels: Record<string, string> = {
    fundamentals: "fundamentalsAnalyst",
    market: "marketAnalyst",
    news: "newsAnalyst",
    social: "socialAnalyst",
  };
  return labels[name] ? t(labels[name]) : name;
}

export function returnViewLabel(t: TFunction, view: ReturnViewName): string {
  const labels: Record<ReturnViewName, string> = {
    timeline: "returnToTimeline",
    deliberation: "returnToDeliberation",
    reports: "returnToReports",
    decision: "returnToDecision",
  };
  return t(labels[view]);
}

export function runDetailPath(
  runId: string,
  values: {
    view?: ViewName;
    report?: string;
    ref?: string;
    return_view?: ReturnViewName;
    return_report?: string;
  },
): string {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  const query = params.toString();
  return `/runs/${encodeURIComponent(runId)}${query ? `?${query}` : ""}`;
}

export function cleanupLabel(
  trashedAt: string,
  retentionDays: number,
  t: TFunction,
) {
  const deadline = trashDeadline(trashedAt, retentionDays);
  if (!deadline) return t("trashRetentionDisabled");
  return t("scheduledCleanup", {
    date: formatUtcDate(deadline.deletionAt),
    remaining: deadline.due
      ? t("trashCleanupDue")
      : t("trashDaysRemaining", { count: deadline.remainingDays }),
  });
}
