import { useTranslation } from "react-i18next";

import type {
  DecisionNumericAuditAppendix,
  ResearchDecision,
} from "../api/client";
import type { EvidenceReferenceIndex } from "../evidence";
import { formatDecisionNumber } from "../numericDisplay";
import EvidenceLinks from "./EvidenceLinks";
import { MarkdownList } from "./AnalystReportView";
import Markdown from "./Markdown";
import NumericAuditAppendixView from "./NumericAuditAppendixView";

export default function ResearchDecisionView({
  decision,
  numericAudit,
  evidenceIndex,
  onEvidence,
  onOpenWarnings,
}: {
  decision: ResearchDecision | null;
  numericAudit?: DecisionNumericAuditAppendix | null;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
  onOpenWarnings?: () => void;
}) {
  const { t } = useTranslation();
  if (!decision) {
    return (
      <article
        className="panel audit-panel"
        id="run-view-decision"
        role="tabpanel"
      >
        <div className="empty-state">{t("noDecision")}</div>
      </article>
    );
  }
  return (
    <article
      className="panel audit-panel decision-panel-v2"
      id="run-view-decision"
      role="tabpanel"
    >
      <ResearchDecisionContent
        decision={decision}
        numericAudit={numericAudit}
        evidenceIndex={evidenceIndex}
        onEvidence={onEvidence}
        onOpenWarnings={onOpenWarnings}
      />
    </article>
  );
}

export function ResearchDecisionContent({
  decision,
  numericAudit,
  evidenceIndex,
  onEvidence,
  onOpenWarnings,
  embedded = false,
}: {
  decision: ResearchDecision;
  numericAudit?: DecisionNumericAuditAppendix | null;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
  onOpenWarnings?: () => void;
  embedded?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const numberLanguage = i18n.resolvedLanguage ?? i18n.language;
  const visibleRefs = (refs: string[]) => (embedded ? [] : refs);
  const scenarios = [...decision.scenarios].sort(
    (left, right) =>
      scenarioOrder.indexOf(left.kind) - scenarioOrder.indexOf(right.kind),
  );
  const calculationUses = buildCalculationUses(decision, t);

  return (
    <div className={embedded ? "decision-content embedded" : "decision-content"}>
      <header className="decision-hero">
        <div className="decision-rating-v2">
          <span>{t("researchRating")}</span>
          <strong>{decision.rating}</strong>
          <small>
            {t("confidence")} {Math.round(decision.confidence * 100)}%
          </small>
        </div>
        <div className="decision-summary">
          <span className="research-opinion-notice">
            {t("nonPersonalizedResearchOpinion")}
          </span>
          <h2>{t("executiveSummary")}</h2>
          <Markdown
            evidenceAliases={evidenceIndex.aliases}
            onEvidence={onEvidence}
          >
            {decision.executive_summary}
          </Markdown>
          <h3>{t("thesis")}</h3>
          <Markdown
            evidenceAliases={evidenceIndex.aliases}
            onEvidence={onEvidence}
          >
            {decision.thesis}
          </Markdown>
          <div className="decision-horizon-summary">
            <strong>{t("horizon")}</strong>
            <Markdown
              evidenceAliases={evidenceIndex.aliases}
              onEvidence={onEvidence}
            >
              {decision.time_horizon}
            </Markdown>
          </div>
          <EvidenceLinks
            refs={visibleRefs(decision.evidence_refs ?? [])}
            evidenceIndex={evidenceIndex}
            onEvidence={onEvidence}
          />
        </div>
      </header>

      {(decision.numeric_audit_status === "partial" ||
        decision.numeric_audit_status === "incomplete") && (
        <div className="numeric-audit-notice" role="status">
          <span>
            {t(
              decision.numeric_audit_status === "partial"
                ? "numericAuditPartial"
                : "numericAuditIncomplete",
            )}
          </span>
          {onOpenWarnings && (
            <button type="button" onClick={onOpenWarnings}>
              {t("openRunWarnings")}
            </button>
          )}
        </div>
      )}

      <section className="decision-section">
        <div className="decision-section-heading">
          <div>
            <p className="eyebrow">{t("conditionalAnalysis")}</p>
            <h2>{t("scenarios")}</h2>
          </div>
        </div>
        <div className="scenario-grid">
          {scenarios.map((scenario) => (
            <article
              className={`scenario-card scenario-${scenario.kind}`}
              key={scenario.kind}
            >
              <header>
                <span>{t(scenarioKey(scenario.kind))}</span>
              </header>
              {(scenario.reference_ranges ?? []).length > 0 && (
                <div className="scenario-reference-ranges">
                  <strong>{t("scenarioReferenceRanges")}</strong>
                  {(scenario.reference_ranges ?? []).map(
                    (referenceRange, index) => (
                      <div
                        className="scenario-reference-range"
                        key={`${referenceRange.category}:${referenceRange.label}:${index}`}
                      >
                        <div className="scenario-reference-heading">
                          <span className="scenario-range-category">
                            {t(`scenarioRangeCategory.${referenceRange.category}`)}
                          </span>
                          <span>{referenceRange.label}</span>
                          <strong
                            title={`${referenceRange.low.value}–${referenceRange.high.value}${referenceRange.unit ? ` ${referenceRange.unit}` : ""}`}
                          >
                            {formatRange(
                              referenceRange.low.value,
                              referenceRange.high.value,
                              referenceRange.unit ?? undefined,
                              numberLanguage,
                            )}
                          </strong>
                        </div>
                        <small className="numeric-date-line">
                          {latestEndpointDate(
                            referenceRange.low.as_of_date,
                            referenceRange.high.as_of_date,
                          )}
                          <TemporalBasisBadge
                            basis={latestTemporalBasis(
                              referenceRange.low.temporal_basis,
                              referenceRange.high.temporal_basis,
                            )}
                          />
                        </small>
                        <div className="scenario-endpoint-bases">
                          <span
                            className={`reference-basis basis-${referenceRange.low.basis}`}
                          >
                            {t(`marketReferenceBasis.${referenceRange.low.basis}`)}
                          </span>
                          {referenceRange.high.basis !==
                            referenceRange.low.basis && (
                            <span
                              className={`reference-basis basis-${referenceRange.high.basis}`}
                            >
                              {t(`marketReferenceBasis.${referenceRange.high.basis}`)}
                            </span>
                          )}
                        </div>
                        <Markdown
                          evidenceAliases={evidenceIndex.aliases}
                          onEvidence={onEvidence}
                        >
                          {referenceRange.interpretation}
                        </Markdown>
                        <EvidenceLinks
                          refs={visibleRefs([
                            ...referenceRange.low.evidence_refs,
                            ...referenceRange.high.evidence_refs,
                          ])}
                          evidenceIndex={evidenceIndex}
                          onEvidence={onEvidence}
                          compact
                          label={false}
                        />
                      </div>
                    ),
                  )}
                </div>
              )}
              <h3>{t("scenarioOutcome")}</h3>
              <Markdown
                evidenceAliases={evidenceIndex.aliases}
                onEvidence={onEvidence}
              >
                {scenario.outcome}
              </Markdown>
              <MarkdownList
                title={t("coreAssumptions")}
                items={scenario.core_assumptions}
                evidenceIndex={evidenceIndex}
                onEvidence={onEvidence}
              />
              <EvidenceLinks
                refs={visibleRefs(scenario.evidence_refs ?? [])}
                evidenceIndex={evidenceIndex}
                onEvidence={onEvidence}
                compact
              />
            </article>
          ))}
        </div>
      </section>

      {decision.valuation_assessment && (
        <section className="decision-section valuation-section">
          <article className="valuation-card">
              <span className="decision-section-label">
                {t("valuationAssessment")}
              </span>
              <h2
                title={`${decision.valuation_assessment.low.value}–${decision.valuation_assessment.high.value} ${decision.valuation_assessment.unit}`}
              >
                {formatRange(
                  decision.valuation_assessment.low.value,
                  decision.valuation_assessment.high.value,
                  decision.valuation_assessment.unit,
                  numberLanguage,
                )}
              </h2>
              <dl>
                <div>
                  <dt>{t("method")}</dt>
                  <dd>{decision.valuation_assessment.method}</dd>
                </div>
                <div>
                  <dt>{t("asOfDate")}</dt>
                  <dd>
                    {latestEndpointDate(
                      decision.valuation_assessment.low.as_of_date,
                      decision.valuation_assessment.high.as_of_date,
                    )}
                    <TemporalBasisBadge
                      basis={latestTemporalBasis(
                        decision.valuation_assessment.low.temporal_basis,
                        decision.valuation_assessment.high.temporal_basis,
                      )}
                    />
                  </dd>
                </div>
              </dl>
              <MarkdownList
                title={t("limitations")}
                items={decision.valuation_assessment.limitations}
                evidenceIndex={evidenceIndex}
                onEvidence={onEvidence}
              />
              <EvidenceLinks
                refs={visibleRefs([
                  ...decision.valuation_assessment.low.evidence_refs,
                  ...decision.valuation_assessment.high.evidence_refs,
                ])}
                evidenceIndex={evidenceIndex}
                onEvidence={onEvidence}
                compact
              />
          </article>
        </section>
      )}

      {(decision.market_reference_levels ?? []).length > 0 && (
        <section className="decision-section market-reference-section">
          <span className="decision-section-label">
            {t("marketReferenceLevels")}
          </span>
          <p className="reference-level-notice">
            {t("marketReferenceNotice")}
          </p>
          <div className="market-reference-table-wrap">
            <table
              className="market-reference-table"
              aria-label={t("marketReferenceLevels")}
            >
              <thead>
                <tr>
                  <th>{t("referenceItem")}</th>
                  <th>{t("value")}</th>
                  <th>{t("asOfDate")}</th>
                  <th>{t("referenceBasis")}</th>
                  <th>{t("interpretation")}</th>
                  <th>{t("evidence")}</th>
                </tr>
              </thead>
              <tbody>
                {(decision.market_reference_levels ?? []).map(
                  (level, index) => (
                    <tr key={`${level.label}:${index}`}>
                      <th data-label={t("referenceItem")}>{level.label}</th>
                      <td
                        className="market-reference-value"
                        data-label={t("value")}
                        title={String(level.value)}
                      >
                        {formatDecisionNumber(
                          level.value,
                          level.unit ?? undefined,
                          numberLanguage,
                        )}
                        {level.unit && ` ${level.unit}`}
                      </td>
                      <td className="market-reference-date" data-label={t("asOfDate")}>
                        {level.as_of_date}
                        <TemporalBasisBadge basis={level.temporal_basis} />
                      </td>
                      <td data-label={t("referenceBasis")}>
                        <span
                          className={`reference-basis basis-${level.basis ?? "observed"}`}
                        >
                          {t(`marketReferenceBasis.${level.basis ?? "observed"}`)}
                        </span>
                      </td>
                      <td className="market-reference-interpretation" data-label={t("interpretation")}>
                        <Markdown
                          evidenceAliases={evidenceIndex.aliases}
                          onEvidence={onEvidence}
                        >
                          {level.interpretation}
                        </Markdown>
                      </td>
                      <td className="market-reference-evidence" data-label={t("evidence")}>
                        <EvidenceLinks
                          refs={visibleRefs(level.evidence_refs)}
                          evidenceIndex={evidenceIndex}
                          onEvidence={onEvidence}
                          label={false}
                          compact
                        />
                      </td>
                    </tr>
                  ),
                )}
              </tbody>
            </table>
          </div>
        </section>
      )}

      <section className="decision-section decision-lists-grid">
        <MarkdownList
          title={t("catalysts")}
          items={decision.catalysts ?? []}
          empty={t("noCatalystsIdentified")}
          evidenceIndex={evidenceIndex}
          onEvidence={onEvidence}
        />
        <MarkdownList
          title={t("risks")}
          items={decision.risks}
          evidenceIndex={evidenceIndex}
          onEvidence={onEvidence}
        />
        <MarkdownList
          title={t("invalidation")}
          items={decision.invalidation_conditions}
          evidenceIndex={evidenceIndex}
          onEvidence={onEvidence}
        />
        <MarkdownList
          title={t("unresolvedQuestions")}
          items={decision.unresolved_questions ?? []}
          empty={t("noneRecorded")}
          evidenceIndex={evidenceIndex}
          onEvidence={onEvidence}
        />
      </section>

      {(decision.risk_review_adjustments ?? []).length > 0 && (
        <section className="decision-section">
          <h2>{t("riskReviewAdjustments")}</h2>
          <div className="adjustment-list">
            {(decision.risk_review_adjustments ?? []).map(
              (adjustment, index) => (
                <article
                  className={`adjustment-card disposition-${adjustment.disposition}`}
                  key={`${adjustment.source_role}:${index}`}
                >
                  <header>
                    <span>{humanize(adjustment.source_role)}</span>
                    <strong>{t(dispositionKey(adjustment.disposition))}</strong>
                  </header>
                  <h3>{adjustment.subject}</h3>
                  <Markdown
                    evidenceAliases={evidenceIndex.aliases}
                    onEvidence={onEvidence}
                  >
                    {adjustment.explanation}
                  </Markdown>
                  <EvidenceLinks
                    refs={visibleRefs(adjustment.evidence_refs ?? [])}
                    evidenceIndex={evidenceIndex}
                    onEvidence={onEvidence}
                    label={false}
                    compact
                  />
                </article>
              ),
            )}
          </div>
        </section>
      )}

      {!embedded &&
        (numericAudit || (decision.calculation_records ?? []).length > 0) && (
        <section className="decision-section numeric-audit-section">
          <NumericAuditAppendixView
            appendix={numericAudit}
            calculationRecords={decision.calculation_records ?? []}
            calculationUses={calculationUses}
            evidenceIndex={evidenceIndex}
            onEvidence={onEvidence}
          />
        </section>
      )}

    </div>
  );
}

const scenarioOrder = ["base", "bull", "bear"] as const;

function scenarioKey(kind: ResearchDecision["scenarios"][number]["kind"]) {
  return {
    base: "baseScenario",
    bull: "bullScenario",
    bear: "bearScenario",
  }[kind];
}

function dispositionKey(
  disposition: NonNullable<
    ResearchDecision["risk_review_adjustments"]
  >[number]["disposition"],
) {
  return {
    retained: "adjustmentRetained",
    modified: "adjustmentModified",
    rejected: "adjustmentRejected",
  }[disposition];
}

function formatRange(
  low: number,
  high: number,
  currency?: string,
  language?: string,
): string {
  return `${formatDecisionNumber(low, currency, language)}–${formatDecisionNumber(
    high,
    currency,
    language,
  )}${
    currency ? ` ${currency}` : ""
  }`;
}

function buildCalculationUses(
  decision: ResearchDecision,
  t: (key: string, options?: Record<string, unknown>) => string,
): Map<string, string[]> {
  const uses = new Map<string, string[]>();
  const add = (ids: string[] | undefined, label: string) => {
    (ids ?? []).forEach((id) => {
      const labels = uses.get(id) ?? [];
      if (!labels.includes(label)) labels.push(label);
      uses.set(id, labels);
    });
  };
  (decision.calculation_records ?? []).forEach((calculation) =>
    (calculation.decision_uses ?? []).forEach((use) =>
      add([calculation.id], decisionCalculationUseLabel(use.component_path, use.label, t)),
    ),
  );
  decision.scenarios.forEach((scenario) =>
    add(
      (scenario.reference_ranges ?? []).flatMap((referenceRange) =>
        [
          referenceRange.low.calculation_id,
          referenceRange.high.calculation_id,
        ].filter((value): value is string => Boolean(value)),
      ),
      t("calculationUseScenario", { scenario: t(scenarioKey(scenario.kind)) }),
    ),
  );
  if (decision.valuation_assessment) {
    add(
      [
        decision.valuation_assessment.low.calculation_id,
        decision.valuation_assessment.high.calculation_id,
      ].filter((value): value is string => Boolean(value)),
      t("valuationAssessment"),
    );
  }
  (decision.market_reference_levels ?? []).forEach((level) =>
    add(
      level.calculation_ids,
      t("calculationUseMarketReference", { label: level.label }),
    ),
  );
  return uses;
}

function decisionCalculationUseLabel(
  componentPath: string,
  label: string,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  let location = t("calculationUseDecisionClaim");
  if (componentPath === "executive_summary") {
    location = t("executiveSummary");
  } else if (componentPath === "thesis") {
    location = t("thesis");
  } else if (componentPath.startsWith("risks.")) {
    location = t("risks");
  } else if (componentPath.startsWith("invalidation_conditions.")) {
    location = t("invalidationConditions");
  } else if (componentPath.startsWith("risk_review_adjustments.")) {
    location = t("riskReviewAdjustments");
  } else {
    const match = /^scenarios\.(base|bull|bear)\./.exec(componentPath);
    if (match) {
      location = t("calculationUseScenario", {
        scenario: t(scenarioKey(match[1] as "base" | "bull" | "bear")),
      });
    }
  }
  return t("calculationUseDecision", { location, label });
}

function latestEndpointDate(left: string, right: string): string {
  return left >= right ? left : right;
}

function latestTemporalBasis(
  left: "point_in_time" | "live_snapshot" | undefined,
  right: "point_in_time" | "live_snapshot" | undefined,
): "point_in_time" | "live_snapshot" {
  return left === "live_snapshot" || right === "live_snapshot"
    ? "live_snapshot"
    : "point_in_time";
}

function TemporalBasisBadge({
  basis,
}: {
  basis: "point_in_time" | "live_snapshot" | undefined;
}) {
  const { t } = useTranslation();
  if (basis !== "live_snapshot") return null;
  return <span className="live-snapshot-badge">{t("liveSnapshot")}</span>;
}

function humanize(value: string): string {
  return value.replaceAll("_", " ");
}
