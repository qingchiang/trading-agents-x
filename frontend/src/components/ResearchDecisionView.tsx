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
                {scenario.reference_range && (
                  <strong>
                    {formatRange(
                      scenario.reference_range.low.value,
                      scenario.reference_range.high.value,
                      scenario.reference_range.unit,
                    )}
                  </strong>
                )}
              </header>
              {scenario.reference_range && (
                <div className="scenario-reference-range">
                  <strong>{t("scenarioReferenceRange")}</strong>
                  <span>{scenario.reference_range.label}</span>
                  <small className="numeric-date-line">
                    {latestEndpointDate(
                      scenario.reference_range.low.as_of_date,
                      scenario.reference_range.high.as_of_date,
                    )}
                    <TemporalBasisBadge
                      basis={latestTemporalBasis(
                        scenario.reference_range.low.temporal_basis,
                        scenario.reference_range.high.temporal_basis,
                      )}
                    />
                  </small>
                  <Markdown
                    evidenceAliases={evidenceIndex.aliases}
                    onEvidence={onEvidence}
                  >
                    {scenario.reference_range.interpretation}
                  </Markdown>
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
              <h2>
                {formatRange(
                  decision.valuation_assessment.low.value,
                  decision.valuation_assessment.high.value,
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
                      <td className="market-reference-value" data-label={t("value")}>
                        {level.value.toLocaleString()} {level.unit}
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

      {(decision.calculation_records ?? []).length > 0 && (
        <section className="decision-section">
          <details className="calculation-records">
            <summary>{t("decisionCalculations")}</summary>
            <div className="calculation-record-list">
              {(decision.calculation_records ?? []).map((calculation) => (
                <article key={calculation.id}>
                  <header>
                    <div>
                      <strong>
                        {calculationUses.get(calculation.id)?.join(" · ") ??
                          t("calculationUnlinked")}
                      </strong>
                      <small>{calculation.id}</small>
                    </div>
                    <span title={String(calculation.result)}>
                      {formatCalculationValue(calculation.result)} {calculation.unit}
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

function formatCalculationValue(value: number): string {
  return value.toLocaleString(undefined, { maximumFractionDigits: 6 });
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
  decision.scenarios.forEach((scenario) =>
    add(
      scenario.reference_range
        ? [
            scenario.reference_range.low.calculation_id,
            scenario.reference_range.high.calculation_id,
          ].filter((value): value is string => Boolean(value))
        : [],
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
