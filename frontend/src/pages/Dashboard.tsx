import ExpandableText from "../components/ExpandableText";
import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { api, type RunSummaryView } from "../api/client";
import { InstrumentIdentity } from "../components/Instruments";
import ResearchRatingBadge from "../components/ResearchRatingBadge";
import StatusBadge from "../components/StatusBadge";
import { researchConfidenceLabel } from "../i18n";
import { Link } from "../router";

const loadHealth = () => api.health();
const loadRecent = () => api.timelines(6, 0, "", false, "recent_activity");
const loadWarnings = () => api.timelines(6, 0, "", true);
const loadFailures = () => api.runs("?status=failed&limit=4");
const loadTasks = async () => {
  const pages = await Promise.all(["running", "queued"].map(status => api.runs(`?status=${status}&limit=4`)));
  return pages.flatMap(page => page.items);
};

function Region<T>({ title, to, load, children }: { title: string; to: string; load: () => Promise<T>; children: (data: T) => ReactNode }) {
  const { t } = useTranslation();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    let pending = false;
    const refresh = async () => {
      if (pending) return;
      pending = true;
      try { const next = await load(); if (active) { setData(next); setError(""); } }
      catch (cause) { if (active) setError(String(cause)); }
      finally { pending = false; }
    };
    void refresh();
    const timer = window.setInterval(refresh, 15000);
    return () => { active = false; window.clearInterval(timer); };
  }, [load, revision]);
  return <section className="workbench-region" aria-label={title}>
    <div className="panel-header"><h2>{title}</h2><Link to={to}>{t("viewAll")}</Link></div>
    {error && <div className="alert" role="alert">{error}<button className="button" onClick={() => setRevision(value => value + 1)}>{t("retryLoad")}</button></div>}
    {data ? children(data) : !error && <p role="status">{t("loading")}</p>}
  </section>;
}
function RunRows({ runs }: { runs: RunSummaryView[] }) {
  const { t } = useTranslation();
  if (!runs.length) return <p className="empty-state">{t("noTasks")}</p>;
  return <ul className="workbench-list">{runs.map(run => <li key={run.id}>
    <Link to={`/runs/${run.id}?view=timeline`}><InstrumentIdentity ticker={run.request.ticker} instrumentName={run.instrument_name} instrumentLocalName={run.instrument_local_name} /></Link>
    <time>{run.request.analysis_date}</time><StatusBadge status={run.status} />
    {run.research_kind === "incremental" && <small className="task-cycle-link"><Link to={`/timelines/${encodeURIComponent(run.request.ticker)}?node=${run.full_baseline_run_id}`}>{t("baselineDate")}</Link></small>}
  </li>)}</ul>;
}
export default function Dashboard() {
  const { t } = useTranslation();
  return <section>
    <header className="page-header"><div><h1>{t("dashboard")}</h1><p className="subtitle">{t("workbenchPurpose")}</p></div></header>
    <Region title={t("queue")} to="/runs" load={loadHealth}>{health => <div className="queue-strip"><span>{t("queue")}: {health.queue.queued}</span><span>{t("running")}: {health.queue.running}</span><span>{t(health.status === "ok" ? "healthy" : "disconnected")}</span></div>}</Region>
    <div className="workbench-columns">
      <Region title={t("continueResearch")} to="/timelines" load={loadRecent}>{page => page.items?.length ? <div className="research-summary-list">{page.items.map(item => <article className="research-summary" key={item.instrument}>
        <header><Link to={`/timelines/${encodeURIComponent(item.instrument)}`}><InstrumentIdentity ticker={item.instrument} instrumentName={item.instrument_name} instrumentLocalName={item.instrument_local_name} /></Link><div><ResearchRatingBadge rating={item.primary_rating} />{item.primary_confidence && <small>{researchConfidenceLabel(t, item.primary_confidence)}</small>}</div></header>
        <p className="summary-cycle">{t("primaryCycle")} · {t("baselineDate")}: {item.primary_baseline_date ?? "—"} · {t("primaryCutoff")}: {item.primary_analysis_date ?? "—"}</p>
        {item.primary_thesis && <ExpandableText text={item.primary_thesis} />}
        {item.timeline_warning && <p className="warning-copy">{t("fullResearchRecommended")}</p>}
        {item.latest_completed_cycle_id && item.latest_completed_cycle_id !== item.primary_cycle_id && <p className="other-cycle-notice"><Link to={`/timelines/${encodeURIComponent(item.instrument)}?node=${item.latest_completed_run_id}`}>{t("otherCycleCompleted")}: {item.latest_completed_analysis_date}</Link></p>}
        <footer><Link className="text-link" to={`/timelines/${encodeURIComponent(item.instrument)}`}>{t("openResearch")}</Link>{item.primary_cycle_id && item.primary_head_run_id && <Link className="text-link" to={`/runs/new?intent=update&from_run=${item.primary_head_run_id}&full_baseline_run_id=${item.primary_cycle_id}`}>{t("updateThisResearch")}</Link>}</footer>
      </article>)}</div> : <p className="empty-state">{t("libraryEmpty")} <Link to="/runs/new">{t("newRun")}</Link></p>}</Region>
      <aside className="workbench-support">
        <Region title={t("workbenchTasks")} to="/runs" load={loadTasks}>{runs => <RunRows runs={runs} />}</Region>
        <Region title={t("failedTasks")} to="/runs?status=failed" load={loadFailures}>{page => <RunRows runs={page.items} />}</Region>
        <Region title={t("workbenchWarnings")} to="/timelines?warning_only=true" load={loadWarnings}>{page => page.items?.length ? <ul className="workbench-list">{page.items.map(item => <li key={item.instrument}><Link to={`/timelines/${encodeURIComponent(item.instrument)}`}><InstrumentIdentity ticker={item.instrument} instrumentName={item.instrument_name} instrumentLocalName={item.instrument_local_name} /></Link><span className="warning-copy">{t("fullResearchRecommended")}</span></li>)}</ul> : <p className="empty-state">{t("noResearchWarnings")}</p>}</Region>
      </aside>
    </div>
  </section>;
}
