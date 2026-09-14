import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  CalculationRecord,
  DecisionNumericAuditAppendix,
  NumericAuditOmission,
  NumericRequirementCheck,
  NumericAuditSnapshot,
} from "../api/client";
import type { EvidenceReferenceIndex } from "../evidence";
import { formatDecisionNumber } from "../numericDisplay";
import EvidenceLinks from "./EvidenceLinks";
import JsonRecord from "./JsonRecord";

export default function NumericAuditAppendixView({
  appendix,
  calculationRecords,
  calculationUses,
  evidenceIndex,
  onEvidence,
}: {
  appendix?: DecisionNumericAuditAppendix | null;
  calculationRecords: CalculationRecord[];
  calculationUses: Map<string, string[]>;
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
  const otherCalculations = calculationRecords.filter(
    (calculation) => !checkedCalculationIds.has(calculation.id),
  );

  return (
    <section className="diagnostic-block numeric-audit-appendix">
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
        <p className="numeric-audit-boundary" role="note">
          {t(
            checks.length > 0
              ? "numericRequirementBoundary"
              : hasSnapshots
                ? "unverifiedNumericBoundary"
                : omissions.length > 0
                  ? "numericAuditGapBoundary"
                  : "formalCalculationBoundary",
          )}
        </p>

        {checks.length > 0 ? (
          <RequirementChecks checks={checks} language={i18n.language} />
        ) : (
          <p className="numeric-requirement-empty">
            {t("numericRequirementNotRecorded")}
          </p>
        )}

        {otherCalculations.length > 0 && <details><summary>{t("auditCalculations")} · {otherCalculations.length}</summary>
          <OtherCalculations
            calculations={otherCalculations}
            calculationUses={calculationUses}
            evidenceIndex={evidenceIndex}
            onEvidence={onEvidence}
            language={i18n.language}
          />
        </details>}

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

function OtherCalculations({
  calculations,
  calculationUses,
  evidenceIndex,
  onEvidence,
  language,
}: {
  calculations: CalculationRecord[];
  calculationUses: Map<string, string[]>;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
  language: string;
}) {
  const { t } = useTranslation();
  return (
    <section className="numeric-other-calculations">
      <h3>{t("otherVerifiedCalculations")}</h3>
      <div className="calculation-record-list">
        {calculations.map((calculation) => (
          <article key={calculation.id}>
            <header>
              <div>
                <strong>
                  {calculation.decision_uses
                    ?.map((use) => use.label)
                    .filter(
                      (label, index, labels) => labels.indexOf(label) === index,
                    )
                    .join(" · ") ||
                    t("otherVerifiedCalculations")}
                </strong>
                <small>{t("numericCalculationStatus.verified")}</small>
              </div>
              <span title={String(calculation.result)}>
                {formatDecisionNumber(
                  calculation.result,
                  calculation.unit,
                  language,
                )}{" "}
                {calculation.unit}
              </span>
            </header>
            <dl className="calculation-record-summary">
              <div>
                <dt>{t("calculationUseLocation")}</dt>
                <dd>
                  {calculationUses.get(calculation.id)?.join(" · ") ?? "—"}
                </dd>
              </div>
              <div>
                <dt>{t("asOfDate")}</dt>
                <dd>{calculation.as_of_date}</dd>
              </div>
            </dl>
            <details className="numeric-calculation-detail">
              <summary>{t("formulaAndEvidence")}</summary>
              <dl>
                <div>
                  <dt>{t("calculationId")}</dt>
                  <dd><code>{calculation.id}</code></dd>
                </div>
                <div>
                  <dt>{t("formula")}</dt>
                  <dd><code>{calculation.formula}</code></dd>
                </div>
                <div>
                  <dt>{t("inputs")}</dt>
                  <dd>
                    <dl className="calculation-inputs">
                      {Object.entries(calculation.inputs).map(([name, value]) => (
                        <div key={name}>
                          <dt><code>{name}</code></dt>
                          <dd title={String(value)}>
                            {formatDecisionNumber(value, undefined, language)}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  </dd>
                </div>
                {calculation.temporal_basis && (
                  <div>
                    <dt>{t("temporalBasis")}</dt>
                    <dd><code>{calculation.temporal_basis}</code></dd>
                  </div>
                )}
                {calculation.limitations.length > 0 && (
                  <div>
                    <dt>{t("limitations")}</dt>
                    <dd>{calculation.limitations.join(" · ")}</dd>
                  </div>
                )}
              </dl>
              <EvidenceLinks
                refs={calculation.input_evidence_refs}
                evidenceIndex={evidenceIndex}
                onEvidence={onEvidence}
                compact
              />
            </details>
          </article>
        ))}
      </div>
    </section>
  );
}

function RequirementChecks({
  checks,
  language,
}: {
  checks: NumericRequirementCheck[];
  language: string;
}) {
  const { t } = useTranslation();
  return (
    <section className="numeric-requirement-checks">
      <h3>{t("decisionRequirementComparisons")}</h3>
      <div className="numeric-requirement-grid">
        {checks.map((check) => (
          <article
            className={`numeric-requirement-check display-${check.display_status} calculation-${check.calculation_status}`}
            key={check.requirement_id}
          >
            <header>
              <div>
                <strong>{check.label}</strong>
                <code>{check.component_path}</code>
              </div>
              <div className="numeric-requirement-statuses">
                <span>{t(`numericCalculationStatus.${check.calculation_status}`)}</span>
                <span>{t(`numericDisplayStatus.${check.display_status}`)}</span>
              </div>
            </header>
            <dl className="numeric-requirement-summary">
              <div>
                <dt>{t("statedValue")}</dt>
                <dd>{formatDecisionNumber(check.stated_value, check.unit, language)} {check.unit}</dd>
              </div>
              <div>
                <dt>{t("canonicalResult")}</dt>
                <dd>
                  {check.comparison_result == null
                    ? "—"
                    : `${formatDecisionNumber(check.comparison_result, check.unit, language)} ${check.unit}`}
                </dd>
              </div>
              <div>
                <dt>{t("comparisonPrecision")}</dt>
                <dd>{check.fraction_digits}</dd>
              </div>
              <div>
                <dt>{t("roundedComparison")}</dt>
                <dd>
                  {check.rounded_stated_value == null ||
                  check.rounded_canonical_result == null
                    ? "—"
                    : `${check.rounded_stated_value} / ${check.rounded_canonical_result}`}
                </dd>
              </div>
            </dl>
            {check.display_status === "mismatched" && (
              <p className="numeric-display-mismatch-note">
                {t("numericDisplayMismatchExplanation")}
              </p>
            )}
            <details className="numeric-requirement-detail">
              <summary>{t("fullCalculationAudit")}</summary>
              <dl>
                <div><dt>{t("rawStatedValue")}</dt><dd><code>{check.stated_value}</code></dd></div>
                <div><dt>{t("rawCanonicalResult")}</dt><dd><code>{check.canonical_result ?? "—"}</code></dd></div>
                <div><dt>{t("comparisonResult")}</dt><dd><code>{check.comparison_result ?? "—"}</code></dd></div>
                <div><dt>{t("comparisonDifference")}</dt><dd><code>{check.comparison_difference ?? "—"}</code></dd></div>
                <div><dt>{t("displayScale")}</dt><dd><code>{check.display_scale}</code></dd></div>
                <div><dt>{t("formula")}</dt><dd><code>{check.formula}</code></dd></div>
                <div><dt>{t("inputs")}</dt><dd><JsonRecord label={t("inputs")} value={check.inputs} /></dd></div>
                <div><dt>{t("evidence")}</dt><dd><code>{check.input_evidence_refs.join(", ")}</code></dd></div>
              </dl>
              <IssueCodes issues={check.issue_codes ?? []} />
            </details>
          </article>
        ))}
      </div>
    </section>
  );
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
