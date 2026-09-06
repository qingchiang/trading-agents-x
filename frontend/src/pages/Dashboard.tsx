import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { api, type RunSummaryView } from "../api/client";
import { InstrumentIdentity } from "../components/Instruments";
import ResearchRatingBadge from "../components/ResearchRatingBadge";
import StatusBadge from "../components/StatusBadge";
import { Link } from "../router";

const loadHealth = () => api.health();
const loadRecent = () => api.runs("?status=succeeded&limit=6");
const loadWarnings = () => api.timelines(6, 0, "", true);
const loadTasks = async () => {
  const pages = await Promise.all(["running", "queued"].map(status => api.runs(`?status=${status}&limit=6`)));
  return pages.flatMap(page => page.items);
};

function Region<T>({ title, to, load, children }: { title: string; to: string; load: () => Promise<T>; children: (data: T) => ReactNode }) {
  const { t } = useTranslation();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try { const next = await load(); if (active) { setData(next); setError(""); } }
      catch (cause) { if (active) setError(String(cause)); }
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
function RunRows({ runs, completed = false }: { runs: RunSummaryView[]; completed?: boolean }) {
  const { t } = useTranslation();
  if (!runs.length) return <p className="empty-state">{t(completed ? "libraryEmpty" : "noActiveTasks")}</p>;
  return <ul className="workbench-list">{runs.map(run => <li key={run.id}>
    <Link to={completed && run.is_research_node ? `/timelines/${encodeURIComponent(run.request.ticker)}?node=${encodeURIComponent(run.id)}` : `/runs/${run.id}?view=timeline`}>
      <InstrumentIdentity ticker={run.request.ticker} instrumentName={run.instrument_name} instrumentLocalName={run.instrument_local_name} />
    </Link><span>{run.request.analysis_date}</span>{completed ? <ResearchRatingBadge rating={run.research_rating} /> : <StatusBadge status={run.status} />}
  </li>)}</ul>;
}
export default function Dashboard() {
  const { t } = useTranslation();
  return <section>
    <header className="page-header"><h1>{t("dashboard")}</h1></header>
    <Region title={t("queue")} to="/runs" load={loadHealth}>{health => <div className="queue-strip"><span>{t("queue")}: {health.queue.queued}</span><span>{t("running")}: {health.queue.running}</span><span>{t(health.status === "ok" ? "healthy" : "disconnected")}</span></div>}</Region>
    <Region title={t("workbenchRecent")} to="/timelines" load={loadRecent}>{page => <RunRows runs={page.items} completed />}</Region>
    <Region title={t("workbenchWarnings")} to="/timelines?warning_only=true" load={loadWarnings}>{page => page.items?.length ? <ul className="workbench-list">{page.items.map(item => <li key={item.instrument}><Link to={`/timelines/${encodeURIComponent(item.instrument)}`}><InstrumentIdentity ticker={item.instrument} instrumentName={item.instrument_name} instrumentLocalName={item.instrument_local_name} /></Link><span>{item.primary_analysis_date ?? "—"}</span><span className="warning-copy">{t("fullResearchRecommended")}</span></li>)}</ul> : <p className="empty-state">{t("noResearchWarnings")}</p>}</Region>
    <Region title={t("workbenchTasks")} to="/runs?status=running" load={loadTasks}>{runs => <RunRows runs={runs} />}</Region>
  </section>;
}
