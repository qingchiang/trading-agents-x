import { useReadingPosition } from "./useReadingPosition";
import { Fragment, useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type ResearchTimelinePage } from "../../shared/api/client";
import { InstrumentIdentity } from "../../shared/Instruments";
import ResearchRatingBadge from "./ResearchRatingBadge";
import { researchConfidenceLabel } from "../../shared/i18n";
import CycleHistory from "./CycleHistory";
import type { TimelineDetail } from "../../shared/api/client";
import { Link, useLocation, useNavigate } from "../../app/router";

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
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true;
    setLoading(true); setError("");
    const timer = window.setTimeout(() => {
      void api.timelines(25, offset, q, warningOnly).then(value => {
        if (active) { setPage(value); setLoading(false); }
      }, cause => { if (active) { setError(String(cause)); setLoading(false); } });
    }, 180);
    return () => { active = false; window.clearTimeout(timer); };
  }, [q, warningOnly, offset, revision]);
  const cycleViewKey = `${location.key}:${location.search}`;
  const [readyCycles, setReadyCycles] = useState<string | null>(null);
  const cyclesLoaded = useCallback(() => setReadyCycles(cycleViewKey), [cycleViewKey]);
  const hasExpandedCycles = page?.items?.some(item => item.instrument === params.get("expanded"));
  // Expanded history supplies most of the page height; preserve the saved target until it renders.
  useReadingPosition(`tradingagents-library:${location.search}`, undefined,
    Boolean(page) && !loading && (!hasExpandedCycles || readyCycles === cycleViewKey));
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
    {page && loading && <p role="status">{t("updatingResults")}</p>}
    {error && <div role="alert" className="alert">{error}<button className="button" onClick={() => setRevision(value => value + 1)}>{t("retryLoad")}</button></div>}
    {!page ? (!error && <div className="loading" role="status">{t("loading")}</div>)
      : page.items?.length ? <div className="table-wrap"><table className="research-library-table">
        <thead><tr><th>{t("ticker")}</th><th>{t("researchRating")}</th><th>{t("primaryCutoff")}</th><th>{t("historyNavigation")}</th></tr></thead>
        <tbody>{page.items.map(item => <Fragment key={item.instrument}><tr>
          <td><Link to={`/timelines/${encodeURIComponent(item.instrument)}`}><InstrumentIdentity ticker={item.instrument} instrumentName={item.instrument_name} instrumentLocalName={item.instrument_local_name} /></Link></td>
          <td data-label={t("researchRating")}><ResearchRatingBadge rating={item.primary_rating} />{item.primary_confidence && <small className="secondary-line">{researchConfidenceLabel(t, item.primary_confidence)}</small>}{item.timeline_warning && <small className="warning-copy secondary-line">{t("fullResearchRecommended")}</small>}</td>
          <td data-label={t("primaryCutoff")}>{item.primary_analysis_date ?? "—"}<small className="secondary-line">{t("baselineDate")}: {item.primary_baseline_date ?? "—"}</small></td><td data-label={t("historyNavigation")}><small>{t("latestResearch")}: {item.latest_analysis_date}</small><small className="secondary-line">{t("fullResearch")}: {item.full_cycle_count} · {t("incrementalResearch")}: {item.incremental_node_count ?? 0}</small><button className="text-button" aria-expanded={params.get("expanded") === item.instrument} onClick={() => update({ expanded: params.get("expanded") === item.instrument ? null : item.instrument, cycle_offset: null })}>{t("historyNavigation")}</button></td>
        </tr>{params.get("expanded") === item.instrument && <tr><td colSpan={4}><LibraryCycles key={cycleViewKey} onLoaded={cyclesLoaded} instrument={item.instrument} offset={Math.max(0, Number(params.get("cycle_offset")) || 0)} onOffset={value => update({ cycle_offset: String(value) })} /></td></tr>}</Fragment>)}</tbody></table></div>
      : <div className="empty-state"><p>{t(q || warningOnly ? "searchEmpty" : "libraryEmpty")}</p>{q || warningOnly ? <button className="button" onClick={() => update({ q: null, warning_only: null, offset: null })}>{t("clearFilters")}</button> : <Link className="button primary" to="/runs/new">{t("newRun")}</Link>}</div>}
    {page && (page.total > page.limit || offset > 0) && <div className="pagination">
      <button className="button" disabled={!offset} onClick={() => update({ offset: String(Math.max(0, offset - page.limit)) })}>{t("previous")}</button>
      <span>{t("runRange", { start: page.total ? offset + 1 : 0, end: Math.min(offset + (page.items?.length ?? 0), page.total), total: page.total })}</span>
      <button className="button" disabled={offset + page.limit >= page.total} onClick={() => update({ offset: String(offset + page.limit) })}>{t("next")}</button>
    </div>}
  </section>;
}


function LibraryCycles({ instrument, offset, onOffset, onLoaded }: { instrument: string; offset: number; onOffset: (offset: number) => void; onLoaded: () => void }) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [data, setData] = useState<TimelineDetail | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let active = true; setData(null); setError("");
    api.timeline(instrument, 12, offset).then(value => { if (active) { setData(value); onLoaded(); } }, cause => { if (active) setError(String(cause)); });
    return () => { active = false; };
  }, [instrument, offset, revision, onLoaded]);
  return <div className="library-cycles">{error ? <p role="alert">{error}<button className="button" onClick={() => setRevision(value => value + 1)}>{t("retryLoad")}</button></p> : !data ? <p role="status">{t("loading")}</p> : <>
    <CycleHistory cycles={data.timeline.cycles ?? []} onSelect={id => navigate(`/timelines/${encodeURIComponent(instrument)}?node=${encodeURIComponent(id)}`)} />
    {(data.timeline.cycle_total ?? 0) > 12 && <div className="pagination"><button className="button" disabled={!offset} onClick={() => onOffset(Math.max(0, offset - 12))}>{t("previous")}</button><button className="button" disabled={offset + 12 >= (data.timeline.cycle_total ?? 0)} onClick={() => onOffset(offset + 12)}>{t("next")}</button></div>}
  </>}</div>;
}
