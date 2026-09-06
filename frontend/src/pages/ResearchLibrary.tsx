import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type ResearchTimelinePage } from "../api/client";
import { InstrumentIdentity } from "../components/Instruments";
import ResearchRatingBadge from "../components/ResearchRatingBadge";
import { researchConfidenceLabel } from "../i18n";
import { Link, useLocation, useNavigate } from "../router";

export default function ResearchLibrary() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const params = new URLSearchParams(location.search);
  const q = params.get("q") ?? "";
  const warningOnly = params.get("warning_only") === "true";
  const offset = Math.max(0, Number(params.get("offset")) || 0);
  const [page, setPage] = useState<ResearchTimelinePage | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    setPage(null); setError("");
    const timer = window.setTimeout(() => {
      void api.timelines(25, offset, q, warningOnly).then(value => {
        if (active) setPage(value);
      }, cause => { if (active) setError(String(cause)); });
    }, 180);
    return () => { active = false; window.clearTimeout(timer); };
  }, [q, warningOnly, offset, revision]);
  function update(values: Record<string, string | null>, replace = false) {
    const next = new URLSearchParams(location.search);
    Object.entries(values).forEach(([key, value]) => value ? next.set(key, value) : next.delete(key));
    navigate(`/timelines${next.size ? `?${next}` : ""}`, { replace });
  }
  return <section>
    <header className="page-header"><div><h1>{t("researchTimelines")}</h1><p className="subtitle">{t("researchLibraryHint")}</p></div></header>
    <div className="workbench-toolbar">
      <label><span>{t("searchResearch")}</span><input type="search" value={q} maxLength={200} onChange={event => update({ q: event.target.value, offset: null }, true)} /></label>
      <label className="checkbox-label"><input type="checkbox" checked={warningOnly} onChange={event => update({ warning_only: event.target.checked ? "true" : null, offset: null })} />{t("warningOnly")}</label>
    </div>
    {error ? <div role="alert" className="alert">{error}<button className="button" onClick={() => setRevision(value => value + 1)}>{t("retryLoad")}</button></div>
      : !page ? <div className="loading" role="status">{t("loading")}</div>
      : page.items?.length ? <div className="table-wrap"><table className="research-library-table">
        <thead><tr><th>{t("ticker")}</th><th>{t("researchRating")}</th><th>{t("primaryCutoff")}</th><th>{t("historyNavigation")}</th></tr></thead>
        <tbody>{page.items.map(item => <tr key={item.instrument}>
          <td><Link to={`/timelines/${encodeURIComponent(item.instrument)}`}><InstrumentIdentity ticker={item.instrument} instrumentName={item.instrument_name} instrumentLocalName={item.instrument_local_name} /></Link></td>
          <td><ResearchRatingBadge rating={item.primary_rating} />{item.primary_confidence && <small className="secondary-line">{researchConfidenceLabel(t, item.primary_confidence)}</small>}{item.timeline_warning && <small className="warning-copy secondary-line">{t("fullResearchRecommended")}</small>}</td>
          <td>{item.primary_analysis_date ?? "—"}</td><td><small>{t("latestResearch")}: {item.latest_analysis_date}</small><small className="secondary-line">{t("fullResearch")}: {item.full_cycle_count} · {t("incrementalResearch")}: {item.incremental_node_count ?? 0}</small></td>
        </tr>)}</tbody></table></div>
      : <div className="empty-state"><p>{t(q || warningOnly ? "searchEmpty" : "libraryEmpty")}</p>{q || warningOnly ? <button className="button" onClick={() => update({ q: null, warning_only: null, offset: null })}>{t("clearFilters")}</button> : <Link className="button primary" to="/runs/new">{t("newRun")}</Link>}</div>}
    {page && (page.total > page.limit || offset > 0) && <div className="pagination">
      <button className="button" disabled={!offset} onClick={() => update({ offset: String(Math.max(0, offset - page.limit)) })}>{t("previous")}</button>
      <span>{t("runRange", { start: page.total ? offset + 1 : 0, end: Math.min(offset + (page.items?.length ?? 0), page.total), total: page.total })}</span>
      <button className="button" disabled={offset + page.limit >= page.total} onClick={() => update({ offset: String(offset + page.limit) })}>{t("next")}</button>
    </div>}
  </section>;
}
