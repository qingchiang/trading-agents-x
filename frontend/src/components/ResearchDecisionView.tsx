import { useTranslation } from "react-i18next";

import type {
  DecisionNumericAuditAppendix,
  ResearchDecision,
} from "../api/client";
import type { EvidenceReferenceIndex } from "../evidence";
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
  const { t } = useTranslation();
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
          <EvidenceLinks
            refs={visibleRefs(decision.evidence_refs ?? [])}
            evidenceIndex={evidenceIndex}
            onEvidence={onEvidence}
          />
          <MemoryLinks refs={decision.memory_refs ?? []} />
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
                {scenario.valuation_range && (
                  <strong>
                    {formatRange(
                      scenario.valuation_range.low,
                      scenario.valuation_range.high,
                    )}
                  </strong>
                )}
              </header>
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

      {(decision.valuation_assessment ||
        (decision.market_reference_levels ?? []).length > 0) && (
        <section className="decision-section valuation-reference-grid">
          {decision.valuation_assessment && (
            <article className="valuation-card">
              <span className="decision-section-label">
                {t("valuationAssessment")}
              </span>
              <h2>
                {formatRange(
                  decision.valuation_assessment.valuation_range.low,
                  decision.valuation_assessment.valuation_range.high,
                  decision.valuation_assessment.currency,
                )}
              </h2>
              <dl>
                <div>
                  <dt>{t("method")}</dt>
                  <dd>{decision.valuation_assessment.method}</dd>
                </div>
                <div>
                  <dt>{t("asOfDate")}</dt>
                  <dd>{decision.valuation_assessment.as_of_date}</dd>
                </div>
              </dl>
              <MarkdownList
                title={t("limitations")}
                items={decision.valuation_assessment.limitations}
                evidenceIndex={evidenceIndex}
                onEvidence={onEvidence}
              />
              <EvidenceLinks
                refs={visibleRefs(
                  decision.valuation_assessment.input_evidence_refs,
                )}
                evidenceIndex={evidenceIndex}
                onEvidence={onEvidence}
                compact
              />
            </article>
          )}

          {(decision.market_reference_levels ?? []).length > 0 && (
            <article className="market-reference-card">
              <span className="decision-section-label">
                {t("marketReferenceLevels")}
              </span>
              <p className="reference-level-notice">
                {t("marketReferenceNotice")}
              </p>
              <div className="market-reference-list">
                {(decision.market_reference_levels ?? []).map(
                  (level, index) => (
                    <section key={`${level.label}:${index}`}>
                      <header>
                        <strong>{level.label}</strong>
                        <span>
                          {level.value.toLocaleString()} {level.unit}
                        </span>
                      </header>
                      <small>{level.as_of_date}</small>
                      <Markdown
                        evidenceAliases={evidenceIndex.aliases}
                        onEvidence={onEvidence}
                      >
                        {level.interpretation}
                      </Markdown>
                      <EvidenceLinks
                        refs={visibleRefs(level.evidence_refs)}
                        evidenceIndex={evidenceIndex}
                        onEvidence={onEvidence}
                        label={false}
                        compact
                      />
                    </section>
                  ),
                )}
              </div>
            </article>
          )}
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

      {(decision.calculation_records ?? []).length > 0 && (
        <section className="decision-section">
          <details className="calculation-records">
            <summary>{t("decisionCalculations")}</summary>
            <div className="calculation-record-list">
              {(decision.calculation_records ?? []).map((calculation) => (
                <article key={calculation.id}>
                  <header>
                    <div>
                      <strong>{humanize(calculation.purpose)}</strong>
                      <small>{calculation.id}</small>
                    </div>
                    <span>
                      {calculation.result.toLocaleString()} {calculation.unit}
                    </span>
                  </header>
                  <code>{calculation.formula}</code>
                  <small>{calculation.as_of_date}</small>
                  <dl>
                    {Object.entries(calculation.inputs).map(([name, value]) => (
                      <div key={name}>
                        <dt>{name}</dt>
                        <dd>{value.toLocaleString()}</dd>
                      </div>
                    ))}
                  </dl>
                  <EvidenceLinks
                    refs={visibleRefs(calculation.input_evidence_refs)}
                    evidenceIndex={evidenceIndex}
                    onEvidence={onEvidence}
                    compact
                  />
                </article>
              ))}
            </div>
          </details>
        </section>
      )}

      {!embedded && numericAudit && (
        <section className="decision-section numeric-audit-section">
          <NumericAuditAppendixView appendix={numericAudit} />
        </section>
      )}

      <footer className="decision-horizon">
        <strong>{t("horizon")}</strong>
        <span>{decision.time_horizon}</span>
      </footer>
    </div>
  );
}

function MemoryLinks({ refs }: { refs: string[] }) {
  const { t } = useTranslation();
  if (refs.length === 0) return null;
  return (
    <div className="evidence-ref-group memory-ref-group">
      <strong>{t("memoryRefs")}</strong>
      <div className="evidence-chips">
        {refs.map((ref) => {
          const runId = ref.startsWith("memory:") ? ref.slice(7) : ref;
          const encoded = encodeURIComponent(runId);
          return (
            <a
              href={`/memory?q=${encoded}#memory-${encoded}`}
              aria-label={`${t("openMemory")} ${ref}`}
              key={ref}
            >
              <code>{ref}</code>
            </a>
          );
        })}
      </div>
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

function formatRange(low: number, high: number, currency?: string): string {
  return `${low.toLocaleString()}–${high.toLocaleString()}${
    currency ? ` ${currency}` : ""
  }`;
}

function humanize(value: string): string {
  return value.replaceAll("_", " ");
}
