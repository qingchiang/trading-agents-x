import { useTranslation } from "react-i18next";
import type { ResearchNodeView } from "../api/client";
import { formatResearchDate } from "../researchDate";
import { localizePerformanceReason } from "../i18n";

export default function PerformanceSection({ node, baselineDate }: { node: ResearchNodeView; baselineDate?: string }) {
  const { t, i18n } = useTranslation();
  const performance = node.performance;
  if (!performance) return null;
  const percent = (value: number) => new Intl.NumberFormat(i18n.language, { style: "percent", maximumFractionDigits: 2, signDisplay: "exceptZero" }).format(value);
  const number = (value: number) => new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 4 }).format(value);
  const rows = [{ name: t("currentInstrument"), stock: true, component: performance.stock, difference: null as number | null }, ...(performance.benchmarks ?? []).map(item => ({ name: item.name, stock: false, component: item.component, difference: item.reported_difference ?? null }))];
  return <section className="performance-section" aria-labelledby="user-content-workspace-period-performance">
    <h2 id="user-content-workspace-period-performance" tabIndex={-1}>{t("performance")}</h2>
    <p className="secondary-line">{t("baselineDate")}: {formatResearchDate(baselineDate, i18n.language)} → {t("selectedCutoff")}: {formatResearchDate(node.analysis_date, i18n.language)}</p>
    <div className="performance-rows">
      {rows.map(({ name, component, difference, stock }) => <article className="performance-row" key={name}>
        <h3>{name}</h3>
        {component.calculation ? <>
          <div className="performance-endpoints"><span>{t("endpointValues")}</span><strong>{number(component.calculation.start_value)} → {number(component.calculation.end_value)}</strong></div>

          <div className="performance-change"><span>{t("periodChange")}</span><strong className="performance-value">{percent(component.calculation.unrounded_return)}</strong></div>
          {!stock && <div className="performance-difference"><span>{t("reportedBenchmarkDifference")}</span><strong>{difference === null ? "—" : t("percentagePoints", { value: new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 2, signDisplay: "exceptZero" }).format(difference * 100) })}</strong></div>}
          <div className="performance-sessions"><span>{t("actualSessions")}</span><strong>{formatResearchDate(component.calculation.start_session, i18n.language)} → {formatResearchDate(component.calculation.end_session, i18n.language)}</strong></div>
          <p className="performance-basis">{component.calculation.provider} · {t(`adjustment_${component.calculation.adjustment_basis}`, { defaultValue: component.calculation.adjustment_basis })}{component.calculation.fallback ? ` · ${t("fallback")}` : ""}</p>
          <p className="performance-retrieved">{t("retrievedAt")}: {formatResearchDate(component.calculation.retrieved_at, i18n.language)}</p>
        </> : <p className="performance-missing"><strong>{t(`performance_${component.status}`)}</strong> · <span>{localizePerformanceReason(t, component.reason)}</span></p>}
      </article>)}
    </div>
  </section>;
}
