import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  CalculationRecord,
  DecisionNumericAuditAppendix,
  NumericAuditOmission,
  NumericRequirementCheck,
  NumericAuditSnapshot,
} from "../../shared/api/client";
import type { EvidenceReferenceIndex } from "./evidence";
import { formatDecisionNumber } from "./numericDisplay";
import EvidenceLinks from "./EvidenceLinks";
import JsonRecord from "../../shared/JsonRecord";
import { Link } from "../../app/router";
import { formatResearchDate } from "../../shared/researchDate";

export type AuditBaseline = { runId: string; date: string; calculations: CalculationRecord[]; appendix?: DecisionNumericAuditAppendix | null };

// Compare complete persisted records, including inputs, dates and limitations, not only IDs.
function sameRecord(left: unknown, right: unknown): boolean {
  if (left === right) return true;
  if (!left || !right || typeof left !== 'object' || typeof right !== 'object') return false;
  if (Array.isArray(left) !== Array.isArray(right)) return false;
  const a = Object.entries(left), b = Object.entries(right);
  return a.length === b.length && a.every(([key, value]) => Object.prototype.hasOwnProperty.call(right, key) && sameRecord(value, (right as Record<string, unknown>)[key]));
}

export default function NumericAuditAppendixView({
  appendix,
  calculationRecords,
  baseline,
  evidenceIndex,
  onEvidence,
}: {
  appendix?: DecisionNumericAuditAppendix | null;
  calculationRecords: CalculationRecord[];
  baseline?: AuditBaseline;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const snapshots = appendix?.snapshots ?? [];
  const defaultPhase = snapshots.some((item) => item.phase === "repair")
    ? "repair"
    : snapshots.at(-1)?.phase;
  const [phase, setPhase] = useState(defaultPhase);
  const omissions = appendix?.omitted_components ?? [];
  const checks = appendix?.requirement_checks ?? [];
  const snapshot = useMemo(
    () =>
      snapshots.find((item) => item.phase === phase) ?? snapshots.at(-1),
    [snapshots, phase],
  );
  const hasSnapshots = snapshots.length > 0;
  const checkedCalculationIds = new Set(
    checks.flatMap((check) =>
      check.calculation_id ? [check.calculation_id] : [],
    ),
  );
  const rows = [
    ...checks.map(check => ({ check, calculation: calculationRecords.find(item => item.id === check.calculation_id), key: check.requirement_id })),
    ...calculationRecords.filter(item => !checkedCalculationIds.has(item.id)).map(calculation => ({ check: undefined, calculation, key: calculation.id })),
  ];
  const inherited = (calculation?: CalculationRecord) => !!calculation && !!baseline?.calculations.some(item => sameRecord(item, calculation));
  const hasInherited = rows.some(row => inherited(row.calculation));

  return (
    <section className="diagnostic-block numeric-audit-appendix" id="numeric-audit">
      <header className="diagnostic-section-heading">
        <h2>{t("decisionRequirementAudit")}</h2>
        <span className="details-summary-meta">
          <span
            className={`numeric-audit-status status-${appendix?.status ?? "unknown"}`}
          >
            {appendix?.status ? t(`numericAppendixStatus.${appendix.status}`) : t("auditNotRecorded")}
          </span>
        </span>
      </header>
      <div className="numeric-audit-appendix-body">
        <p className="numeric-audit-boundary" role="note">{t(checks.length ? 'numericRequirementBoundary' : omissions.length ? 'numericAuditGapBoundary' : hasSnapshots ? 'unverifiedNumericBoundary' : 'auditNoCurrentChecks')}</p>
        {baseline && <p className="audit-baseline-context"><Link className="text-link" to={`/runs/${encodeURIComponent(baseline.runId)}?view=diagnostics#numeric-audit`}>{t('viewBaselineAudit')} · {formatResearchDate(baseline.date, i18n.language)}</Link>{hasInherited && <span>{t('baselineAuditScope')}</span>}</p>}
        {rows.length ? <section className="numeric-requirement-checks">
          <h3>{t('auditCalculations')} · {rows.length}</h3>
          <div className="numeric-requirement-grid calculation-record-list">{rows.map(({ check, calculation, key }) => {
            const fromBaseline = inherited(calculation);
            const baselineChecks = fromBaseline ? baseline?.appendix?.requirement_checks?.filter(item => item.calculation_id === calculation?.id) ?? [] : [];
            return <CalculationEntry key={key} check={check} calculation={calculation} inherited={fromBaseline} baselineChecks={baselineChecks} evidenceIndex={evidenceIndex} onEvidence={onEvidence} language={i18n.language} />;
          })}</div>
        </section> : <p className="numeric-requirement-empty">{t('auditNoCalculations')}</p>}
        {hasInherited && !!baseline?.appendix?.omitted_components?.length && <section className="numeric-audit-omissions"><h3>{t('baselineAuditOmissions')}</h3><ul>{baseline.appendix.omitted_components.map(item => <li key={item.component_path}><strong>{omissionLabel(item, t)}</strong><code>{item.component_path}</code><IssueCodes issues={item.issue_codes} /></li>)}</ul></section>}

        {omissions.length > 0 && (
          <section className="numeric-audit-omissions">
            <h3>{t("omittedNumericComponents")}</h3>
            <ul>
              {omissions.map((item) => (
                <li key={item.component_path}>
                  <strong>{omissionLabel(item, t)}</strong>
                  <code>{item.component_path}</code>
                  <IssueCodes issues={item.issue_codes} />
                </li>
              ))}
            </ul>
          </section>
        )}

        {snapshot && <details><summary>{t("auditSnapshots")}</summary>
        {snapshots.length > 1 && (
          <div className="numeric-snapshot-tabs" role="group">
            {[...snapshots].reverse().map((item) => (
              <button
                type="button"
                aria-pressed={snapshot?.phase === item.phase}
                className={snapshot?.phase === item.phase ? "active" : ""}
                onClick={() => setPhase(item.phase)}
                key={item.phase}
              >
                {t(`numericSnapshotPhase.${item.phase}`)}
              </button>
            ))}
          </div>
        )}

        <NumericSnapshotView snapshot={snapshot} />
        </details>}
        <JsonRecord label={t("decisionRequirementAudit")} value={appendix} />
      </div>
    </section>
  );
}

function CalculationEntry({ check, calculation, inherited, baselineChecks, language, evidenceIndex, onEvidence }: {
  check?: NumericRequirementCheck; calculation?: CalculationRecord; inherited: boolean; baselineChecks: NumericRequirementCheck[]; language: string;
  evidenceIndex: EvidenceReferenceIndex; onEvidence: (ref: string) => void;
}) {
  const { t } = useTranslation();
  const effectiveChecks = check ? [check] : baselineChecks;
  const mismatch = effectiveChecks.some(item => item.display_status === 'mismatched' || ['missing', 'invalid'].includes(item.calculation_status));
  const location = (path: string) => {
    const [section, ...rest] = path.split('.');
    const label = { thesis: 'thesis', executive_summary: 'executiveSummary', risks: 'risks', scenarios: 'scenarios', valuation_assessment: 'valuationAssessment', market_reference_levels: 'marketReferenceLevels', invalidation_conditions: 'invalidationConditions' }[section];
    return label ? `${t(label)}${rest.length ? ` · ${rest.join('.')}` : ''}` : path;
  };
  const locations = [...new Set([...(check ? [check.component_path] : []), ...(calculation?.decision_uses ?? []).map(use => use.component_path)])];
  const title = check?.label ?? ([...new Set(calculation?.decision_uses?.map(use => use.label))].join(' · ') || t('recordedCalculation'));
  const metadata = <>
    {inherited && <p className="audit-record-source">{t('baselineCalculationUnchanged')}</p>}
    <dl className="calculation-record-summary">
      <div><dt>{t('calculationUseLocation')}</dt><dd>{locations.length ? locations.map(location).join(' · ') : t('notRecorded')}</dd></div>
      {calculation && <><div><dt>{t('recordedCalculationResult')}</dt><dd>{formatDecisionNumber(calculation.result, calculation.unit, language)} {calculation.unit}</dd></div><div><dt>{t('asOfDate')}</dt><dd>{formatResearchDate(calculation.as_of_date, language)}</dd></div></>}
    </dl>
  </>;
  return <article className={`numeric-requirement-check${mismatch ? ' display-mismatched' : ''}`}>
    <header><strong>{title}</strong>{!effectiveChecks.length && <span className="audit-record-source">{t(inherited ? 'baselineCalculationUnchanged' : 'currentCalculationRecord')}</span>}</header>
    {effectiveChecks.length ? <section className="audit-entry-checks">{effectiveChecks.map(item => <div key={item.requirement_id}>
      <div className="audit-check-heading"><h4>{t(check ? 'currentAuditResult' : 'baselineAuditResult')}</h4>
      <div className={`numeric-requirement-statuses display-${item.display_status} calculation-${item.calculation_status}`}><span>{t(`numericCalculationStatus.${item.calculation_status}`)}</span><span>{t(`numericDisplayStatus.${item.display_status}`)}</span></div></div>
      <dl className="numeric-requirement-summary">
        <div><dt>{t('statedValue')}</dt><dd>{formatDecisionNumber(item.stated_value, item.unit, language)} <span className="audit-value-unit">{item.unit}</span></dd></div>
        <div><dt>{t('canonicalResult')}</dt><dd>{item.comparison_result == null ? t('notRecorded') : <>{formatDecisionNumber(item.comparison_result, item.unit, language)} <span className="audit-value-unit">{item.unit}</span></>}</dd></div>
      </dl>
      <details className="numeric-requirement-detail numeric-calculation-detail"><summary>{t('formulaAndEvidence')}</summary>
      {metadata}
      {item.display_status === 'mismatched' && item.calculation_status === 'verified' && <p className="numeric-display-mismatch-note">{t('numericDisplayMismatchExplanation')}</p>}
      <IssueCodes issues={item.issue_codes ?? []} />
      <dl>
        <div><dt>{t('comparisonPrecision')}</dt><dd>{item.fraction_digits}</dd></div>
        <div><dt>{t('roundedComparison')}</dt><dd>{item.rounded_stated_value == null || item.rounded_canonical_result == null ? t('notRecorded') : `${item.rounded_stated_value} / ${item.rounded_canonical_result}`}</dd></div>
        <div><dt>{t('rawStatedValue')}</dt><dd><code>{item.stated_value}</code></dd></div>
        <div><dt>{t('rawCanonicalResult')}</dt><dd><code>{item.canonical_result ?? '—'}</code></dd></div>
        <div><dt>{t('comparisonResult')}</dt><dd><code>{item.comparison_result ?? '—'}</code></dd></div>
        <div><dt>{t('comparisonDifference')}</dt><dd><code>{item.comparison_difference ?? '—'}</code></dd></div>
        <div><dt>{t('displayScale')}</dt><dd><code>{item.display_scale}</code></dd></div>
        <div><dt>{t('formula')}</dt><dd><code>{item.formula}</code></dd></div>
        <div><dt>{t('inputs')}</dt><dd><JsonRecord label={t('inputs')} value={item.inputs} /></dd></div>
      </dl><EvidenceLinks refs={item.input_evidence_refs} evidenceIndex={evidenceIndex} onEvidence={onEvidence} compact /><JsonRecord label={t(check ? 'currentAuditResult' : 'baselineAuditResult')} value={item} />{calculation && <>{calculation.limitations.length > 0 && <p>{calculation.limitations.join(' · ')}</p>}<JsonRecord label={t('recordedCalculation')} value={calculation} /></>}</details>
    </div>)}</section> : <div className="audit-recorded-summary"><span className="secondary-line">{t('auditNoItemCheck')}</span>{calculation && <span>{t('recordedCalculationResult')}: <strong>{formatDecisionNumber(calculation.result, calculation.unit, language)} {calculation.unit}</strong></span>}</div>}
    {calculation && !effectiveChecks.length && <details className="numeric-calculation-detail"><summary>{t('formulaAndEvidence')}</summary>{metadata}<dl>
      <div><dt>{t('calculationId')}</dt><dd><code>{calculation.id}</code></dd></div>
      <div><dt>{t('formula')}</dt><dd><code>{calculation.formula}</code></dd></div>
      <div><dt>{t('inputs')}</dt><dd><JsonRecord label={t('inputs')} value={calculation.inputs} /></dd></div>
      {calculation.temporal_basis && <div><dt>{t('temporalBasis')}</dt><dd><code>{calculation.temporal_basis}</code></dd></div>}
      {!!calculation.limitations.length && <div><dt>{t('limitations')}</dt><dd>{calculation.limitations.join(' · ')}</dd></div>}
    </dl><EvidenceLinks refs={calculation.input_evidence_refs} evidenceIndex={evidenceIndex} onEvidence={onEvidence} compact /><JsonRecord label={t('recordedCalculation')} value={calculation} /></details>}
  </article>;
}

function omissionLabel(
  item: NumericAuditOmission,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  const parts: string[] = [];
  if (item.scenario_kind) {
    parts.push(
      t(
        {
          base: "scenarioBase",
          bull: "scenarioBull",
          bear: "scenarioBear",
        }[item.scenario_kind],
      ),
    );
  }
  parts.push(t(`numericOmissionComponent.${item.component_type}`));
  if (item.reference_label) parts.push(item.reference_label);
  return parts.join(" · ");
}

function NumericSnapshotView({ snapshot }: { snapshot: NumericAuditSnapshot }) {
  const { t } = useTranslation();
  return (
    <section className="numeric-snapshot" role="region">
      <div className="numeric-snapshot-meta">
        <span>
          {t("generationMethod")}: <code>{snapshot.method}</code>
        </span>
        <span>
          {t("failureReason")}: <code>{snapshot.reason_code}</code>
        </span>
        <span>
          {t("schemaValid")}: {snapshot.schema_valid ? t("yes") : t("no")}
        </span>
      </div>
      <IssueCodes issues={snapshot.validation_issues ?? []} />
      {snapshot.candidate ? (
        <JsonRecord label={t("rawNumericCandidate")} value={snapshot.candidate} />
      ) : (
        <p className="numeric-candidate-omitted">
          {snapshot.candidate_omitted === "oversize"
            ? t("numericCandidateOversize", {
                digest: snapshot.candidate_digest ?? "—",
              })
            : t("numericCandidateUnparseable")}
        </p>
      )}
    </section>
  );
}

function IssueCodes({ issues }: { issues: string[] }) {
  const { t } = useTranslation();
  if (issues.length === 0) return null;
  return (
    <div className="numeric-issue-codes" aria-label={t("validationIssues")}>
      {issues.map((issue) => (
        <code key={issue}>{issue}</code>
      ))}
    </div>
  );
}
