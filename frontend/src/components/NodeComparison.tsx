import type { TFunction } from "i18next";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { type ResearchNodeComparison, type ResearchNodeView } from "../api/client";
import { localizePerformanceReason } from "../i18n";
import { Link } from "../router";
import { useReadingPosition } from "../useReadingPosition";
import ComparisonValue, { comparisonLabels, hasAdditionalComparisonFields } from "./ComparisonValue";
import EvidenceSourceDrawer from "./EvidenceSourceDrawer";
import { useComparisonEvidence } from "./useComparisonEvidence";
import { WorkspaceNavigationButtons } from "./ResearchWorkspace";
import ResearchKindBadge from "./ResearchKindBadge";
import ReadingAnchorNotice from "./ReadingAnchorNotice";

type ComparisonSide = ResearchNodeComparison["sides"][number];
type ProductComparisonRow = { key: string; label: string; values: [unknown, unknown] };

export default function NodeComparison({ comparison, baselineDates = {}, primaryCycleId, changedOnly: controlledChangedOnly, onChangedOnly, onSwap, onClose }: {
  comparison: ResearchNodeComparison; baselineDates?: Record<string, string>; primaryCycleId?: string | null;
  changedOnly?: boolean; onChangedOnly?: (value: boolean) => void; onSwap?: () => void; onClose: () => void;
}) {
  const { t } = useTranslation();
  const titleId = useId();
  const [localChangedOnly, setChangedOnly] = useState(true);
  const [swapped, setSwapped] = useState(false);
  const [source, setSource] = useState<{ side: number; ref: string } | null>(null);
  const left = useComparisonEvidence(comparison.sides[0]);
  const right = useComparisonEvidence(comparison.sides[1]);
  const evidence = [left, right];
  const changedOnly = controlledChangedOnly ?? localChangedOnly;
  const order = swapped ? [1, 0] : [0, 1];
  useReadingPosition(`comparison:${comparison.instrument}`);
  const knownSections = comparison.decision_sections.filter(section => comparisonLabels[section.key]);
  const sections = knownSections.flatMap(section => {
    if (section.key !== "scenarios") return [{ ...section, outlineId: `comparison-${section.key}`, label: t(comparisonLabels[section.key]) }];
    const kinds = [...new Set(section.values.flatMap(value => Array.isArray(value.value) ? value.value.map(item => item?.kind).filter((kind): kind is string => typeof kind === "string") : []))];
    return kinds.length ? kinds.map(kind => ({ ...section, outlineId: `comparison-scenarios-${kind}`, label: `${t("scenarios")} · ${["base", "bull", "bear"].includes(kind) ? t(`${kind}Scenario`) : t("comparisonContentUnsupported")}`, values: section.values.map(value => {
      if (value.state !== "recorded" || !Array.isArray(value.value)) return value;
      const items = value.value.filter(item => item?.kind === kind);
      return { state: items.length ? "recorded" as const : "empty" as const, value: items };
    }) })) : [{ ...section, outlineId: "comparison-scenarios", label: t("scenarios") }];
  }).filter(section => !changedOnly || stableJson(section.values[0]) !== stableJson(section.values[1]));
  const technicalFields = comparison.decision_sections.some(section => !comparisonLabels[section.key] || section.values.some(value => hasAdditionalComparisonFields(section.key, value.value)));
  const products = filterProductRows([
    productRow("decision-outcome", t("decisionOutcome"), comparison.sides, side => side.decision_outcome ? `${t(`decisionOutcome_${side.decision_outcome}`)}${side.decision_outcome_reason ? `\n${side.decision_outcome_reason}` : ""}` : null),
    productRow("performance", t("performance"), comparison.sides, side => performanceComparisonText(t, side)),
    productRow("full-research-required", t("fullResearchRecommended"), comparison.sides, side => side.full_research_required_reasons?.map(reason => reason.message).join("\n") || null),
    productRow("advancement", t("informationAdvancement"), comparison.sides, side => side.information_advancement ? advancementSummary(t, side.information_advancement.reasons ?? []) : null),
    productRow("availability", t("researchAvailability"), comparison.sides, side => availabilityComparisonText(t, side)),
    productRow("reassessment", t("reassessment"), comparison.sides, side => reassessmentComparisonText(t, side)),
  ], changedOnly);
  return <section className="workspace-reader comparison-reader" role="region" aria-labelledby={titleId}>
    <ReadingAnchorNotice identity={`${comparison.sides.map(side => side.node_id).join(":")}:${changedOnly}`} />
    <header className="reading-toolbar comparison-toolbar">
      <WorkspaceNavigationButtons />
      <h2 id={titleId}>{t("nodeComparison")}</h2>
      <label className="checkbox-label"><input type="checkbox" checked={changedOnly} onChange={event => (onChangedOnly ?? setChangedOnly)(event.target.checked)} />{t("showChangedOnly")}</label>
      <button className="button" onClick={() => { setSource(null); if (onSwap) onSwap(); else setSwapped(value => !value); }}>{t("swapComparisonSides")}</button>
      <button className="button" onClick={onClose}>{t("close")}</button>
    </header>
    <p className="comparison-context-note">{t(comparison.cross_cycle ? "crossCycleComparison" : "sameCycleComparison")}</p>
    <div className="comparison-baselines">{order.map(index => { const side = comparison.sides[index]; const state = evidence[index]; return <section key={side.node_id}>
      <strong>{side.analysis_date}</strong> <ResearchKindBadge kind={side.research_kind} />
      <p><Link to={`/timelines/${encodeURIComponent(comparison.instrument)}?node=${encodeURIComponent(side.cycle_id)}${side.lifecycle_state === "trashed" ? "&trash_state=all" : ""}`}>{t("baselineDate")}: {baselineDates[side.cycle_id] ?? (side.research_kind === "full" ? side.analysis_date : t("notRecorded"))}</Link></p>
      {primaryCycleId === side.cycle_id && <span className="cycle-primary">{t("primaryCycle")}</span>}
      {side.lifecycle_state === "trashed" && <p>{t("retainedInTrash")}</p>}
      {state.loading && <p role="status">{t("loadingEvidence")}</p>}
      {state.error && <p className="research-limitations" role="alert">{t(state.error)} <button className="text-button" onClick={state.retry}>{t("retryLoad")}</button></p>}
    </section>; })}</div>
    {comparison.method_changed && <p className="notice">{t("methodChanged")}</p>}
    {!!comparison.warnings?.length && <div className="research-limitations">{comparison.warnings.map(warning => <p key={`${warning.code}:${warning.message}`}>{warning.message}</p>)}</div>}
    {sections.map(section => <section className="comparison-section" key={`${section.key}:${section.label}`}>
      <h3 id={section.outlineId} data-outline={section.label}>{section.label}</h3><div className="comparison-side-by-side">{order.map(index => <div key={comparison.sides[index].node_id}>
        <time className="comparison-side-date">{comparison.sides[index].analysis_date}</time>
        <ComparisonValue field={section.key} value={section.values[index]} index={evidence[index].index} onEvidence={ref => setSource({ side: index, ref })} />
      </div>)}</div>
    </section>)}
    {products.map(row => <section className="comparison-section" key={row.key}><h3 id={`comparison-product-${row.key}`} data-outline={row.label}>{row.label}</h3><div className="comparison-side-by-side">{order.map(index => <div key={index}>
      <time className="comparison-side-date">{comparison.sides[index].analysis_date}</time>
      <ComparisonValue field="thesis" value={row.values[index] == null ? { state: "not_recorded_under_this_schema" } : { state: "recorded", value: row.values[index] }} index={evidence[index].index} onEvidence={ref => setSource({ side: index, ref })} />
    </div>)}</div></section>)}
    {!sections.length && !products.length && !technicalFields && <p>{t("comparisonNoChangedSections")}</p>}
    {technicalFields && <p className="notice">{t("comparisonTechnicalFields")}</p>}
    <div className="action-row">{order.map(index => { const side = comparison.sides[index]; return <Link key={side.node_id} to={`/runs/${encodeURIComponent(side.node_id)}?view=diagnostics`}>{side.analysis_date} · {t("runDiagnostics")}</Link>; })}</div>
    <EvidenceSourceDrawer evidenceRef={source?.ref ?? null} evidenceIndex={source ? evidence[source.side].index : left.index} onClose={() => setSource(null)} />
  </section>;
}
function stableJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(stableJson).join(",")}]`;
  }
  if (value && typeof value === "object") {
    return `{${Object.entries(value)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, nested]) => `${JSON.stringify(key)}:${stableJson(nested)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value) ?? "undefined";
}

function productRow(
  key: string,
  label: string,
  sides: ResearchNodeComparison["sides"],
  value: (side: ComparisonSide) => unknown,
): ProductComparisonRow {
  return {
    key,
    label,
    values: [value(sides[0]), value(sides[1])],
  };
}

function filterProductRows(
  rows: ProductComparisonRow[],
  changedOnly: boolean,
) {
  if (!changedOnly) return rows;
  return rows.filter(
    (row) => stableJson(row.values[0]) !== stableJson(row.values[1]),
  );
}

function advancementSummary(t: TFunction, reasons: string[]): string {
  if (reasons.length === 0) return t("noInformationAdvancement");
  const labels: Record<string, string> = {
    admissible_observation: "advancementAdmissibleObservation",
    completed_stock_session: "advancementCompletedMarketSession",
    newly_completed_market_session: "advancementCompletedMarketSession",
    near_live_advisory: "advancementNearLiveAdvisory",
  };
  return reasons
    .map((reason) => t(labels[reason] ?? "advancementOther", { reason }))
    .join(", ");
}

function performanceComponentText(
  t: TFunction,
  label: string,
  component: NonNullable<ResearchNodeView["performance"]>["stock"],
): string {
  if (component.calculation) {
    return `${label}: ${formatPercent(component.calculation.unrounded_return)}`;
  }
  return `${label}: ${t(`performance_${component.status}`)}${
    component.reason ? ` · ${localizePerformanceReason(t, component.reason)}` : ""
  }`;
}

function performanceComparisonText(
  t: TFunction,
  side: ComparisonSide,
): string | null {
  const performance = side.performance;
  if (!performance) return null;
  return [
    performanceComponentText(t, t("stockReturn"), performance.stock),
    ...(performance.benchmarks ?? []).map((benchmark) => {
      const summary = performanceComponentText(
        t,
        benchmark.name,
        benchmark.component,
      );
      return benchmark.reported_difference == null
        ? summary
        : `${summary} · ${t("reportedBenchmarkDifference")}: ${t("percentagePoints", { value: new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(benchmark.reported_difference * 100) })}`;
    }),
  ].join("\n");
}

function availabilityComparisonText(
  t: TFunction,
  side: ComparisonSide,
): string | null {
  const domains = side.research_availability?.domains ?? [];
  if (domains.length === 0) return null;
  return domains
    .map(
      (domain) =>
        `${t(`${domain.domain}Analyst`)}: ${t(
          `availability_${domain.status}`,
        )}`,
    )
    .join(", ");
}

function reassessmentComparisonText(
  t: TFunction,
  side: ComparisonSide,
): string | null {
  const entries = side.reassessment?.entries ?? [];
  if (entries.length === 0) return null;
  return entries
    .map(
      (entry) =>
        `${t(
          `reassessment_${entry.disposition}`,
        )} · ${entry.reason}`,
    )
    .join("\n");
}


function formatPercent(value: number) { return new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: 2 }).format(value); }
