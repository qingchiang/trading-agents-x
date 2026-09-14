import { useContext } from "react";
import { NumericNoticeHandled } from "../researchWarnings";
import { useTranslation } from "react-i18next";
import { researchConfidenceLabel } from "../i18n";

import type { DecisionNumericAuditAppendix, ResearchDecision, } from "../api/client";
import type { EvidenceReferenceIndex } from "../evidence";
import { formatDecisionNumber } from "../numericDisplay";
import { MarkdownList } from "./AnalystReportView";
import EvidenceLinks from "./EvidenceLinks";
import Markdown from "./Markdown";

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
        id="run-view-decision" aria-labelledby="run-tab-decision"
        role="region"
      >
        <div className="empty-state">{t("noDecision")}</div>
      </article>
    );
  }
  return (
    <article
      className="panel audit-panel decision-panel-v2"
      id="run-view-decision" aria-labelledby="run-tab-decision"
      role="region"
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
  const noticeHandled = useContext(NumericNoticeHandled);
  const numberLanguage = i18n.resolvedLanguage ?? i18n.language;
  const visibleRefs = (refs: string[]) => (embedded ? [] : refs);
  const scenarios = [...decision.scenarios].sort(
    (left, right) =>
      scenarioOrder.indexOf(left.kind) - scenarioOrder.indexOf(right.kind),
  );

  return (
    <div className={embedded ? "decision-content embedded" : "decision-content"}>
      <header className="decision-hero">
        <div className="decision-rating-v2">
          <span>{t("researchRating")}</span>
          <strong>{decision.rating}</strong>
          <small>{researchConfidenceLabel(t, decision.confidence)}</small>
        </div>
        <div className="decision-summary">
          <h2 id={embedded ? undefined : "assessment-summary"} data-outline={embedded ? undefined : t("executiveSummary")}>{t("executiveSummary")}</h2>
          <p className="research-opinion-notice">{t("nonPersonalizedResearchOpinion")}</p>
          <Markdown
            evidenceAliases={evidenceIndex.aliases}
            onEvidence={onEvidence}
          >
            {decision.executive_summary}
          </Markdown>
          <h2 id={embedded ? undefined : "assessment-thesis"} data-outline={embedded ? undefined : t("thesis")}>{t("thesis")}</h2>
          <Markdown
            evidenceAliases={evidenceIndex.aliases}
            onEvidence={onEvidence}
          >
            {decision.thesis}
          </Markdown>
          <div className="decision-horizon-summary">
            <span className="decision-horizon-label">{t("horizon")}</span>
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

      {!noticeHandled && ((decision.numeric_audit_status === "partial" || numericAudit?.status === "partial") ||
        decision.numeric_audit_status === "incomplete") && (
        <div className="numeric-audit-notice" role="status">
          <span>
            {t(
              (decision.numeric_audit_status === "partial" || numericAudit?.status === "partial")
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

      <section className="decision-section decision-lists-grid">
        <MarkdownList
          outlineId={embedded ? undefined : "assessment-catalysts"}
          title={t("catalysts")}
          items={decision.catalysts ?? []}
          empty={t("noCatalystsIdentified")}
          evidenceIndex={evidenceIndex}
          onEvidence={onEvidence}
        />
        <MarkdownList
          outlineId={embedded ? undefined : "assessment-risks"}
          title={t("risks")}
          items={decision.risks}
          evidenceIndex={evidenceIndex}
          onEvidence={onEvidence}
        />
        <MarkdownList
          outlineId={embedded ? undefined : "assessment-invalidation"}
          title={t("invalidation")}
          items={decision.invalidation_conditions}
          evidenceIndex={evidenceIndex}
          onEvidence={onEvidence}
        />
        <MarkdownList
          outlineId={embedded ? undefined : "assessment-unresolvedQuestions"}
          title={t("unresolvedQuestions")}
          items={decision.unresolved_questions ?? []}
          empty={t("noneRecorded")}
          evidenceIndex={evidenceIndex}
          onEvidence={onEvidence}
        />
      </section>

      <section className="decision-section">
        <div className="decision-section-heading">
          <div>
            <p className="eyebrow">{t("conditionalAnalysis")}</p>
            <h2 id={embedded ? undefined : "assessment-scenarios"} data-outline={embedded ? undefined : t("scenarios")}>{t("scenarios")}</h2>
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
                          <span className="scenario-range-name">{referenceRange.label}</span>
                          <span className="scenario-range-category">
                            {t(`scenarioRangeCategory.${referenceRange.category}`)}
                          </span>
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
              <h2 id={embedded ? undefined : "assessment-valuation"} data-outline={embedded ? undefined : t("valuationAssessment")}>{t("valuationAssessment")}</h2>
              <p className="valuation-value"
                title={`${decision.valuation_assessment.low.value}–${decision.valuation_assessment.high.value} ${decision.valuation_assessment.unit}`}
              >
                {formatRange(
                  decision.valuation_assessment.low.value,
                  decision.valuation_assessment.high.value,
                  decision.valuation_assessment.unit,
                  numberLanguage,
                )}
              </p>
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
          <h2 id={embedded ? undefined : "assessment-market"} data-outline={embedded ? undefined : t("marketReferenceLevels")}>{t("marketReferenceLevels")}</h2>
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



      {(decision.risk_review_adjustments ?? []).length > 0 && (
        <section className="decision-section">
          <h2 id={embedded ? undefined : "assessment-riskReviewAdjustments"} data-outline={embedded ? undefined : t("riskReviewAdjustments")}>{t("riskReviewAdjustments")}</h2>
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
