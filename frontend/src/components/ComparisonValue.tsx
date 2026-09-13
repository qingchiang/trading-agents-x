import { useTranslation } from "react-i18next";
import type { ResearchNodeComparison } from "../api/client";
import type { EvidenceReferenceIndex } from "../evidence";
import { formatDecisionNumber } from "../numericDisplay";
import { researchConfidenceLabel } from "../i18n";
import Markdown from "./Markdown";

type Value = ResearchNodeComparison["decision_sections"][number]["values"][number];
export const comparisonLabels: Record<string, string> = {
  rating: "researchRating", confidence: "confidence", executive_summary: "executiveSummary", thesis: "thesis",
  catalysts: "catalysts", risks: "risks", invalidation_conditions: "invalidation", unresolved_questions: "unresolvedQuestions",
  time_horizon: "horizon", scenarios: "scenarios", valuation_assessment: "valuationAssessment",
  market_reference_levels: "marketReferenceLevels", risk_review_adjustments: "riskReviewAdjustments",
};
const record = (value: unknown): Record<string, unknown> => value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};

/** Render known research content explicitly; schema details stay in diagnostics. */
export default function ComparisonValue({ field, value, index, onEvidence }: {
  field: string; value?: Value; index: EvidenceReferenceIndex; onEvidence: (ref: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const unavailable = <p className="muted-copy">{t("comparisonContentUnsupported")}</p>;
  const prose = (text: unknown) => {
    if (text === undefined) return <p>{t("notRecorded")}</p>;
    if (text === null) return <p>{t("comparisonNull")}</p>;
    if (typeof text !== "string") return unavailable;
    const aliases = { ...index.aliases };
    for (const match of text.matchAll(/\bev_[a-f0-9]{12}\b/g)) aliases[match[0]] ??= "?";
    return <Markdown evidenceAliases={aliases} onEvidence={onEvidence}>{text}</Markdown>;
  };
  const list = (items: unknown) => items === undefined ? <p>{t("notRecorded")}</p> : items === null ? <p>{t("comparisonNull")}</p> : Array.isArray(items) ? <ul>{items.map((item, n) => <li key={n}>{prose(item)}</li>)}</ul> : unavailable;
  const refs = (items: unknown) => Array.isArray(items) ? <div className="evidence-ref-list">{[...new Set(items.filter((ref): ref is string => typeof ref === "string"))].map(ref => <button className="inline-evidence-ref" key={ref} onClick={() => onEvidence(ref)} aria-label={`${t("openEvidence")} ${index.aliases[ref] ?? t("evidenceReferenceUnavailable")}`}>{index.aliases[ref] ?? t("evidenceReferenceUnavailable")}</button>)}</div> : null;
  const endpoint = (input: unknown, unit: unknown) => {
    const item = record(input);
    return <span>{typeof item.value === "number" ? formatDecisionNumber(item.value, typeof unit === "string" ? unit : undefined, i18n.language) : t("notRecorded")}{typeof unit === "string" && ` ${unit}`}<small className="secondary-line">{typeof item.as_of_date === "string" ? item.as_of_date : t("notRecorded")}{item.temporal_basis === "live_snapshot" && ` · ${t("liveSnapshot")}`}</small>{refs([...(Array.isArray(item.evidence_refs) ? item.evidence_refs : []), ...(Array.isArray(item.date_evidence_refs) ? item.date_evidence_refs : [])])}</span>;
  };
  const range = (input: unknown) => { const item = record(input); return <div className="comparison-range">{endpoint(item.low, item.unit)}<span>–</span>{endpoint(item.high, item.unit)}</div>; };
  const limitations = (input: unknown) => Array.isArray(input) && input.length ? <div className="research-limitations"><strong>{t("limitations")}</strong>{list(input)}</div> : null;
  if (!value || value.state === "not_recorded_under_this_schema") return <p>{t("notRecordedUnderThisSchema")}</p>;
  if (value.state === "null") return <p>{t("comparisonNull")}</p>;
  if (value.state === "empty") return <p>{t("comparisonEmpty")}</p>;
  const input = value.value;
  if (field === "confidence" && (input === "low" || input === "medium" || input === "high")) return <p>{researchConfidenceLabel(t, input)}</p>;
  if (["catalysts", "risks", "invalidation_conditions", "unresolved_questions"].includes(field)) return list(input);
  if (field === "scenarios") {
    if (!Array.isArray(input)) return unavailable;
    return <>{input.map((raw, n) => { const item = record(raw); return <article className="comparison-scenario" key={n}>
      <h4>{["base", "bull", "bear"].includes(String(item.kind)) ? t(`${item.kind}Scenario`) : t("comparisonContentUnsupported")}</h4>{prose(item.outcome)}
      <h4>{t("coreAssumptions")}</h4>{list(item.core_assumptions)}
      {Array.isArray(item.reference_ranges) && item.reference_ranges.map((rawRange, j) => { const r = record(rawRange); return <section key={j}><h4>{typeof r.label === "string" ? r.label : t("scenarioReferenceRange")}</h4>{range(r)}{prose(r.interpretation)}{limitations(r.limitations)}</section>; })}{refs(item.evidence_refs)}
    </article>; })}</>;
  }
  if (field === "valuation_assessment") { const item = record(input); return <>{range(item)}{prose(item.method)}{limitations(item.limitations)}</>; }
  if (field === "market_reference_levels") return Array.isArray(input) ? <>{input.map((raw, n) => { const item = record(raw); return <section key={n}><h4>{typeof item.label === "string" ? item.label : t("referenceItem")}</h4>{endpoint(item, item.unit)}{typeof item.basis === "string" && <p>{t(`marketReferenceBasis.${item.basis}`, { defaultValue: t("comparisonContentUnsupported") })}</p>}{prose(item.interpretation)}</section>; })}</> : unavailable;
  if (field === "risk_review_adjustments") return Array.isArray(input) ? <>{input.map((raw, n) => { const item = record(raw); return <section key={n}>{prose(item.subject)}{typeof item.disposition === "string" && item.disposition.length > 0 && <p>{t(`adjustment${item.disposition[0].toUpperCase()}${item.disposition.slice(1)}`)}</p>}{prose(item.explanation)}{refs(item.evidence_refs)}</section>; })}</> : unavailable;
  return typeof input === "string" ? prose(input) : unavailable;
}

/** Detect schema extensions without presenting technical keys as research prose. */
export function hasAdditionalComparisonFields(field: string, value: unknown): boolean {
  const extra = (item: unknown, fields: string[]) => Object.keys(record(item)).some(key => !fields.includes(key));
  const endpointFields = ["value", "as_of_date", "evidence_refs", "date_evidence_refs", "basis", "calculation_id", "source_locator", "temporal_basis"];
  const rangeFields = ["low", "high", "unit", "measurement_kind", "limitations"];
  const rangeExtra = (input: unknown, fields: string[]) => {
    const item = record(input);
    return extra(item, fields) || extra(item.low, endpointFields) || extra(item.high, endpointFields);
  };
  if (field === "scenarios" && Array.isArray(value)) return value.some(input => {
    const item = record(input);
    return !["base", "bull", "bear"].includes(String(item.kind)) || extra(item, ["kind", "outcome", "core_assumptions", "evidence_refs", "reference_ranges"])
      || Array.isArray(item.reference_ranges) && item.reference_ranges.some(range => rangeExtra(range, [...rangeFields, "category", "label", "interpretation"]));
  });
  if (field === "valuation_assessment") return rangeExtra(value, [...rangeFields, "method"]);
  if (field === "market_reference_levels" && Array.isArray(value)) return value.some(item => extra(item, [...endpointFields, "calculation_ids", "interpretation", "label", "measurement_kind", "unit"]));
  if (field === "risk_review_adjustments" && Array.isArray(value)) return value.some(item => extra(item, ["subject", "disposition", "explanation", "source_role", "evidence_refs"]));
  return false;
}
