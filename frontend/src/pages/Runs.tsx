import { useEffect, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { api, type RunGroupPage, type RunSummaryView } from "../api/client";
import { InstrumentIdentity } from "../components/Instruments";
import { ActionMenu, tabsKeyDown } from "../components/Interaction";
import RunLifecycleDialog, { type LifecycleAction } from "../components/RunLifecycleDialog";
import ResearchKindBadge from "../components/ResearchKindBadge";
import ResearchRatingBadge from "../components/ResearchRatingBadge";
import StatusBadge from "../components/StatusBadge";
import { researchConfidenceLabel } from "../i18n";
import { Link, useLocation, useNavigate } from "../router";
import { formatUtcDate, trashDeadline } from "../trash";

export default function Runs() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const params = new URLSearchParams(location.search);
  const trash = params.get("trash_state") === "trashed";
  const offset = Math.max(0, Number(params.get("offset")) || 0);
  const [page, setPage] = useState<RunGroupPage | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [revision, setRevision] = useState(0);
  const [retention, setRetention] = useState(30);
  const [selected, setSelected] = useState<string[]>([]);
  const [operation, setOperation] = useState<{ ids: string[]; action: LifecycleAction } | null>(null);
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const [query, setQuery] = useState(params.get("q") ?? "");
  const [status, setStatus] = useState(params.get("status") ?? "");
  const [kind, setKind] = useState(params.get("research_kind") ?? "");
  useEffect(() => { let active = true; api.capabilities().then(value => { if (active) setRetention(value.defaults.trash_retention_days); }).catch(() => {}); return () => { active = false; }; }, []);
  useEffect(() => {
    let active = true;
    const search = new URLSearchParams(location.search);
    setQuery(search.get("q") ?? ""); setStatus(search.get("status") ?? ""); setKind(search.get("research_kind") ?? "");
    setPage(null); setSelected([]); setError("");
    search.set("limit", "12"); search.set("offset", String(offset)); search.set("trash_state", trash ? "trashed" : "active");
    api.runGroups(`?${search}`).then(value => { if (active) setPage(value); }, cause => { if (active) setError(String(cause)); });
    return () => { active = false; };
  }, [location.search, revision]);
  const update = (values: Record<string, string | null>) => {
    const next = new URLSearchParams(location.search);
    for (const [key, value] of Object.entries(values)) value === null || value === "" ? next.delete(key) : next.set(key, value);
    navigate(`/runs${next.size ? `?${next}` : ""}`);
  };
  const filter = (event: FormEvent) => { event.preventDefault(); update({ q: query.trim(), status, research_kind: kind, offset: null }); };
  const act = async (run: RunSummaryView, action: "cancel" | "retry") => {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError("");
    try { await api.action(run.id, action); setRevision(value => value + 1); }
    catch (cause) { setError(String(cause)); }
    finally { lock.current = false; setBusy(false); }
  };
  const researchLink = (run: RunSummaryView) => run.is_research_node ? `/timelines/${encodeURIComponent(run.request.ticker)}?node=${encodeURIComponent(run.id)}${run.trashed_at ? "&trash_state=all" : ""}` : `/runs/${run.id}?view=decision`;
  const row = (run: RunSummaryView, matched: boolean, baseline = false) => <div className={`task-row ${baseline ? "baseline" : ""} ${matched ? "matched" : "context"}`} key={run.id}>
    {!run.is_research_node && <input type="checkbox" aria-label={t("selectRun", { ticker: run.request.ticker })} disabled={!trash && ["queued", "running"].includes(run.status)} checked={selected.includes(run.id)} onChange={() => setSelected(value => value.includes(run.id) ? value.filter(id => id !== run.id) : [...value, run.id])} />}
    <div><time>{run.request.analysis_date}</time><ResearchKindBadge kind={run.research_kind} /></div>
    <div className="decision-cell"><ResearchRatingBadge rating={run.research_rating} />{run.research_confidence && <small>{researchConfidenceLabel(t, run.research_confidence)}</small>}</div>
    <StatusBadge status={run.status} />
    {run.trashed_at && <small>{retention ? formatUtcDate(trashDeadline(run.trashed_at, retention)!.deletionAt) : t("trashRetentionDisabled")}</small>}
    <div className="task-actions">
      <Link className="text-link" to={`/runs/${run.id}?view=timeline`}>{t("executionDetails")}</Link>
      {run.status === "succeeded" && <Link className="text-link" to={researchLink(run)}>{t("openResearch")}</Link>}
      <ActionMenu label={t("manageResearch")}>
        {!run.trashed_at && ["queued", "running"].includes(run.status) && <button className="button" disabled={busy || run.cancel_requested} onClick={() => void act(run, "cancel")}>{t("cancel")}</button>}
        {!run.trashed_at && run.status === "failed" && <button className="button" disabled={busy} onClick={() => void act(run, "retry")}>{t("retry")}</button>}
        {!run.trashed_at && !["queued", "running"].includes(run.status) && <button className="button danger" onClick={() => setOperation({ ids: [run.id], action: "trash" })}>{t(baseline ? "moveCycleToTrash" : "moveNodeToTrash")}</button>}
        {run.trashed_at && <><button className="button" onClick={() => setOperation({ ids: [run.id], action: "restore" })}>{t("restore")}</button><button className="button danger" onClick={() => setOperation({ ids: [run.id], action: "purge" })}>{t("confirmPurge")}</button></>}
      </ActionMenu>
    </div>
  </div>;
  return <section>
    <header className="page-header"><div><h1>{t("runManagement")}</h1><p className="subtitle">{t("cycleTasksHint")}</p></div></header>
    <div className="view-tabs" role="tablist" onKeyDown={tabsKeyDown}>{[false, true].map(value => <button role="tab" aria-selected={trash === value} tabIndex={trash === value ? 0 : -1} key={String(value)} onClick={() => update({ trash_state: value ? "trashed" : null, offset: null })}>{t(value ? "trashedRuns" : "activeRuns")}</button>)}</div>
    {trash && <p className="research-limitations"><strong>{t("trashRetentionTitle")}</strong> · {t(retention ? "trashRetentionPolicy" : "trashRetentionDisabled", { count: retention })}</p>}
    <form className="task-filter-bar" onSubmit={filter}>
      <label>{t("runSearch")}<input id="runs-search" type="search" value={query} onChange={event => setQuery(event.target.value)} /></label>
      <label>{t("status")}<select value={status} onChange={event => setStatus(event.target.value)}><option value="">{t("all")}</option>{["queued", "running", "succeeded", "failed", "cancelled"].map(value => <option key={value} value={value}>{t(`status${value[0].toUpperCase()}${value.slice(1)}`)}</option>)}</select></label>
      <label>{t("researchKind")}<select id="runs-kind" value={kind} onChange={event => setKind(event.target.value)}><option value="">{t("all")}</option><option value="full">{t("fullResearch")}</option><option value="incremental">{t("incrementalResearch")}</option></select></label>
      <button className="button" type="submit">{t("apply")}</button>
    </form>
    {selected.length > 0 && <button className="button" onClick={() => setOperation({ ids: selected, action: trash ? "restore" : "trash" })}>{t(trash ? "restoreSelected" : "trashSelected", { count: selected.length })}</button>}
    {notice && <p className="notice" role="status">{notice}</p>}
    {error && <p className="alert" role="alert">{error}<button className="button" onClick={() => setRevision(value => value + 1)}>{t("retryLoad")}</button></p>}
    {!page && !error && <p role="status">{t("loading")}</p>}
    {page && !page.items.length && <p className="empty-state">{t(params.get("q") || params.get("status") || params.get("research_kind") ? "searchEmpty" : trash ? "noTrashedRuns" : "noActiveRuns")}</p>}
    {page?.items.map(group => <article className="task-group" key={group.id}>
      <header className="task-group-heading"><InstrumentIdentity ticker={group.instrument} instrumentName={group.baseline?.instrument_name ?? (group.related_tasks ?? [])[0]?.instrument_name} instrumentLocalName={group.baseline?.instrument_local_name ?? (group.related_tasks ?? [])[0]?.instrument_local_name} />
        <div>{group.baseline ? <span>{t("baselineDate")}: {group.baseline.request.analysis_date}</span> : <span>{t("standaloneTask")}</span>}{group.is_primary && <strong className="cycle-primary">{t("primaryCycle")}</strong>}<small>{t("matchingTasks", { count: (group.matched_run_ids ?? []).length })}</small></div>
      </header>
      <div className="task-group-counts">{Object.entries(group.status_counts ?? {}).filter(([, count]) => count > 0).map(([state, count]) => <span key={state}>{t(`status${state[0].toUpperCase()}${state.slice(1)}`)} {count}</span>)}{group.cycle_warning && <span className="warning-copy">{t("fullResearchRecommended")}</span>}</div>
      {(group.research_runs ?? []).map(run => row(run, (group.matched_run_ids ?? []).includes(run.id), run.id === group.baseline?.id))}
      {(group.related_tasks ?? []).length > 0 && <div className={group.baseline ? "related-tasks" : "standalone-tasks"}>{group.baseline && <p>{t("relatedTasksHint")}</p>}{(group.related_tasks ?? []).map(run => row(run, (group.matched_run_ids ?? []).includes(run.id)))}</div>}
    </article>)}
    {page && (page.total > page.limit || offset > 0) && <div className="pagination"><button className="button" disabled={!offset} onClick={() => update({ offset: String(Math.max(0, offset - page.limit)) })}>{t("previous")}</button><span>{t("groupRange", { start: page.total ? offset + 1 : 0, end: Math.min(offset + page.items.length, page.total), total: page.total })}</span><button className="button" disabled={offset + page.limit >= page.total} onClick={() => update({ offset: String(offset + page.limit) })}>{t("next")}</button></div>}
    {operation && <RunLifecycleDialog runIds={operation.ids} action={operation.action} onClose={() => setOperation(null)} onDone={changed => { setNotice(t(operation.action === "trash" ? "runsTrashed" : operation.action === "restore" ? "runsRestored" : "researchRemoved", { count: changed })); setOperation(null); setSelected([]); if (offset) update({ offset: "0" }); else setRevision(value => value + 1); }} />}
  </section>;
}
