import { useTranslation } from "react-i18next";
import type { ResearchNodeView } from "../api/client";
import { localizePerformanceReason } from "../i18n";

export default function PerformanceSection({ node, baselineDate }: { node: ResearchNodeView; baselineDate?: string }) {
  const { t, i18n } = useTranslation();
  const performance = node.performance;
  if (!performance) return null;
  const percent = (value: number) => new Intl.NumberFormat(i18n.language, { style: "percent", maximumFractionDigits: 2, signDisplay: "exceptZero" }).format(value);
  const number = (value: number) => new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 4 }).format(value);
  const rows = [{ name: t("currentInstrument"), component: performance.stock, difference: null as number | null }, ...(performance.benchmarks ?? []).map(item => ({ name: item.name, component: item.component, difference: item.reported_difference ?? null }))];
  return <section className="performance-section" aria-labelledby="user-content-workspace-period-performance">
    <h2 id="user-content-workspace-period-performance" tabIndex={-1}>{t("performance")}</h2>
    <p className="secondary-line">{t("baselineDate")}: {baselineDate ?? "—"} → {t("selectedCutoff")}: {node.analysis_date}</p>
    <div className="performance-rows">
      {rows.map(({ name, component, difference }) => <article className="performance-row" key={name}>
        <h3>{name}</h3>
        {component.calculation ? <>
          <div><span>{t("actualSessions")}</span><strong>{component.calculation.start_session} → {component.calculation.end_session}</strong></div>
          <div><span>{t("periodChange")}</span><strong className="performance-value">{percent(component.calculation.unrounded_return)}</strong></div>
          <div><span>{t("reportedBenchmarkDifference")}</span><strong>{difference === null ? "—" : t("percentagePoints", { value: new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 2, signDisplay: "exceptZero" }).format(difference * 100) })}</strong></div>
          <p className="performance-basis">{t("adjustmentBasis")}: {component.calculation.adjustment_basis}</p>
          <details className="performance-calculation"><summary>{t("calculationDetails")}</summary><dl>
            <dt>{t("endpointValues")}</dt><dd>{number(component.calculation.start_value)} → {number(component.calculation.end_value)}</dd>
            <dt>{t("source")}</dt><dd>{component.calculation.provider}{component.calculation.fallback ? ` · ${t("fallback")}` : ""}</dd>
            <dt>{t("retrievedAt")}</dt><dd>{component.calculation.retrieved_at}</dd>
          </dl></details>
        </> : <p className="performance-missing"><strong>{t(`performance_${component.status}`)}</strong> · <span>{localizePerformanceReason(t, component.reason)}</span></p>}
      </article>)}
    </div>
  </section>;
}
