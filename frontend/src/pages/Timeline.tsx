import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type ResearchNodeView, type ResearchNodeComparison, type ResearchNodeComparisonSelection, type TimelineDetail, type ResearchTimelinePage } from "../api/client";
import { Link, useLocation, useNavigate } from "../router";
import { InstrumentIdentity } from "../components/Instruments";
import ResearchRatingBadge from "../components/ResearchRatingBadge";
import ResearchKindBadge from "../components/ResearchKindBadge";
import ConfirmDialog from "../components/ConfirmDialog";
import { ActionMenu } from "../components/Interaction";
import { researchConfidenceLabel } from "../i18n";
const RunDetail = lazy(() => import("./RunDetail"));
const NodeComparison = lazy(() => import("../components/NodeComparison"));
const CYCLE_PAGE_SIZE = 12;

export default function Timeline() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const params = useMemo(() => new URLSearchParams(location.search), [location.search]);
  const isList = location.pathname === "/timelines";
  const instrument = decodeURIComponent(location.pathname.split("/").at(-1) ?? "");
  const requestedNode = params.get("node") ?? "";
  const showRetainedTrash = params.get("trash_state") === "all";
  const cycleOffset = Math.max(0, Number(params.get("cycle_offset")) || 0);
  const comparisonMode = params.get("compare_mode") === "1";
  const selections = params.getAll("compare").slice(0, 2).map(value => {
    const split = value.indexOf(":");
    return { node_id: value.slice(split + 1), lifecycle_state: value.slice(0, split) === "trashed" ? "trashed" : "active" } as ResearchNodeComparisonSelection;
  });
  const [detail, setDetail] = useState<TimelineDetail | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [comparison, setComparison] = useState<ResearchNodeComparison | null>(null);
  const [busy, setBusy] = useState(false);
  const [pendingNode, setPendingNode] = useState<ResearchNodeView | null>(null);
  const [lifecycleMode, setLifecycleMode] = useState<"trash" | "purge" | null>(null);
  const [replacementPrimary, setReplacementPrimary] = useState("");
  const update = (values: Record<string, string | null>, replace = false) => {
    const next = new URLSearchParams(params);
    Object.entries(values).forEach(([key, value]) => { if (value === null) next.delete(key); else next.set(key, value); });
    navigate(`${location.pathname}${next.size ? `?${next}` : ""}`, { replace });
  };
  const [timelines, setTimelines] = useState<ResearchTimelinePage | null>(null);
  const [listOffset, setListOffset] = useState(0);
  const [query, setQuery] = useState("");
  const filteredTimelines = (timelines?.items ?? []).filter(item => `${item.instrument} ${item.instrument_name ?? ""} ${item.instrument_local_name ?? ""}`.toLowerCase().includes(query.toLowerCase()));
  useEffect(() => {
    let active = true;
    setError(""); setDetail(null); setComparison(null);
    if (isList) {
      void api.timelines(50, listOffset).then(value => { if (active) setTimelines(value); }, cause => { if (active) setError(String(cause)); });
    } else {
      void api.timeline(instrument, CYCLE_PAGE_SIZE, cycleOffset, showRetainedTrash ? "all" : "active", requestedNode || undefined)
        .then(value => {
          if (!active) return;
          setDetail(value);
          const actual = value.timeline.cycle_offset ?? 0;
          if (actual !== cycleOffset) {
            const next = new URLSearchParams(location.search);
            if (actual) next.set("cycle_offset", String(actual)); else next.delete("cycle_offset");
            navigate(`${location.pathname}?${next}`, { replace: true });
          }
        }, cause => { if (active) setError(cause instanceof Error ? cause.message : String(cause)); });
    }
    return () => { active = false; };
  }, [isList, instrument, cycleOffset, requestedNode, showRetainedTrash, listOffset, revision]);
  const cycles = detail?.timeline.cycles ?? [];
  const nodes = cycles.flatMap(cycle => [cycle.baseline, ...(cycle.increments ?? [])]);
  const primaryCycle = cycles.find(cycle => cycle.is_primary);
  const selected = requestedNode ? nodes.find(node => node.id === requestedNode)
    : nodes.find(node => node.id === primaryCycle?.head_run_id) ?? nodes.find(node => node.id === cycles[0]?.head_run_id);
  const selectedCycle = cycles.find(cycle => cycle.id === selected?.cycle_id);
  const activeFullCycles = detail?.timeline.active_full_cycles ?? [];
  const closeComparison = useCallback(() => setComparison(null), []);
  const toggleComparison = (node: ResearchNodeView) => {
    const next = new URLSearchParams(params);
    next.delete("compare");
    const remaining = selections.some(item => item.node_id === node.id)
      ? selections.filter(item => item.node_id !== node.id)
      : [...selections, { node_id: node.id, lifecycle_state: node.is_active ? "active" : "trashed" }].slice(0, 2);
    remaining.forEach(item => next.append("compare", `${item.lifecycle_state}:${item.node_id}`));
    navigate(`${location.pathname}?${next}`, { replace: true }); setComparison(null);
  };
  const compare = async () => {
    if (selections.length !== 2 || busy) return;
    setBusy(true); setError("");
    try { setComparison(await api.compareResearchNodes(instrument, selections)); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  };
  const lifecycle = async () => {
    if (!pendingNode || !lifecycleMode || busy) return;
    if (lifecycleMode === "trash" && pendingNode.research_kind === "full" && pendingNode.is_primary && activeFullCycles.some(c => c.id !== pendingNode.id) && !replacementPrimary) { setError(t("selectReplacementCycle")); return; }
    setBusy(true);
    try {
      if (lifecycleMode === "purge") await api.purgeRuns([pendingNode.id]);
      else await api.trashRuns([pendingNode.id], replacementPrimary ? { [pendingNode.id]: replacementPrimary } : {});
      setPendingNode(null); setRevision(value => value + 1);
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  };
  const mutate = async (action: () => Promise<unknown>) => {
    setBusy(true); setError("");
    try { await action(); setRevision(value => value + 1); }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { setBusy(false); }
  };
  if (isList) {
    return (
      <section>
        <header className="page-header">
          <div><p className="eyebrow">{t("researchTimeline")}</p><h1>{t("researchTimelines")}</h1><p className="subtitle">{t("researchTimelinesHint")}</p></div>
          <Link className="button primary" to="/runs/new">+ {t("newRun")}</Link>
        </header>
        <div className="workbench-toolbar">
          <label><span>{t("searchResearch")}</span><input value={query} onChange={(event) => setQuery(event.target.value)} /></label>
          <Link className="button" to="/runs">{t("executionHistory")}</Link>
        </div>
        {error && <div className="alert">{error}</div>}
        {!timelines && !error && <div className="loading">{t("loading")}</div>}
        <div className="timeline-list-grid">
          {filteredTimelines.map((item) => (
            <Link className={`timeline-summary-card ${item.timeline_warning ? "warning" : ""}`} to={`/timelines/${encodeURIComponent(item.instrument)}`} key={item.instrument}>
              <InstrumentIdentity ticker={item.instrument} instrumentName={item.instrument_name} instrumentLocalName={item.instrument_local_name} />
              <div className="timeline-summary-decision"><ResearchRatingBadge rating={item.primary_rating} /><Confidence value={item.primary_confidence} /></div>
              <dl><div><dt>{t("fullResearch")}</dt><dd>{item.full_cycle_count}</dd></div><div><dt>{t("incrementalResearch")}</dt><dd>{item.incremental_node_count ?? 0}</dd></div><div><dt>{t("latestResearch")}</dt><dd>{item.latest_analysis_date}</dd></div></dl>
              {item.timeline_warning && <span className="warning-copy">{t("fullResearchRecommended")}</span>}
            </Link>
          ))}
        </div>
        {timelines && timelines.total > timelines.limit && (
          <div className="pagination"><button className="button" disabled={listOffset === 0} onClick={() => setListOffset((value) => Math.max(0, value - timelines.limit))}>← {t("previous")}</button><span>{t("runRange", { start: listOffset + 1, end: Math.min(listOffset + filteredTimelines.length, timelines.total), total: timelines.total })}</span><button className="button" disabled={listOffset + (timelines.items?.length ?? 0) >= timelines.total} onClick={() => setListOffset((value) => value + timelines.limit)}>{t("next")} →</button></div>
        )}
      </section>
    );
  }
  return <section>
    <header className="page-header research-header">
      <div><Link className="back-link" to="/timelines">{t("backToResearch")}</Link>
        <InstrumentIdentity ticker={instrument} instrumentName={detail?.timeline.instrument_name} instrumentLocalName={detail?.timeline.instrument_local_name} prominent />
        {selected && <div className="workspace-context"><span>{t("selectedCutoff")}: {selected.analysis_date}</span><span>{t("baselineDate")}: {selectedCycle?.baseline.analysis_date}</span>
          <span>{t(selectedCycle?.is_primary && selected.is_cycle_head ? "currentPrimary" : "selectedResearch")}</span></div>}
      </div>
      <div className="action-row"><button className="button" onClick={() => update({ compare_mode: comparisonMode ? null : "1", compare: null })}>{t(comparisonMode ? "closeComparisonMode" : "comparisonMode")}</button>
        <ActionMenu label={t("manageResearch")}><button className="button" onClick={() => update({ trash_state: showRetainedTrash ? null : "all", compare: null, cycle_offset: null })}>{t(showRetainedTrash ? "hideRetainedTrash" : "showRetainedTrash")}</button></ActionMenu>
      </div>
    </header>
    {error && <div className="alert" role="alert">{error}<button className="button" onClick={() => setRevision(value => value + 1)}>{t("retryLoad")}</button></div>}
    {!detail && !error && <div className="loading" role="status">{t("loading")}</div>}
    {detail?.timeline.timeline_warning && <p className="research-limitations" role="status">{t("fullResearchRecommended")}</p>}
    {detail && !selected && <div className="empty-state">{t(requestedNode ? "unavailableResearch" : "noCommittedFullResearch")}</div>}
    {comparisonMode && <aside className="comparison-tray" aria-label={t("comparisonSelection")}>
      <span>{t("comparisonSelectionHint")}</span>
      {selections.map((item, index) => <span className="comparison-selection-chip" key={item.node_id}>
        {nodes.find(node => node.id === item.node_id)?.analysis_date ?? `${t("selectedResearch")} ${index + 1}`}
        <button aria-label={t("removeFromComparison")} onClick={() => {
          const next = new URLSearchParams(params); next.delete("compare"); selections.filter(other => other.node_id !== item.node_id).forEach(other => next.append("compare", `${other.lifecycle_state}:${other.node_id}`)); navigate(`${location.pathname}?${next}`, { replace: true });
        }}>×</button></span>)}
      <button className="button primary" disabled={selections.length !== 2 || busy} onClick={() => void compare()}>{t("compareSelectedNodes")}</button>
    </aside>}
    <div className="research-workspace">
      <details className="workspace-history" open><summary>{t("historyNavigation")}</summary>
        <nav aria-label={t("historyNavigation")}>
          {cycles.map(cycle => <details className="history-cycle" open={cycle.is_primary || cycle.id === selected?.cycle_id || comparisonMode} key={cycle.id}>
            <summary>{cycle.baseline.analysis_date} {cycle.is_primary && <small>{t("primaryCycle")}</small>}{cycle.cycle_warning && <small className="warning">{t("fullResearchRecommended")}</small>}</summary>
            {[cycle.baseline, ...(cycle.increments ?? [])].map(node => <div className={`history-node ${node.research_kind}`} key={node.id}>
              <button className="history-select" aria-current={selected?.id === node.id ? "page" : undefined} onClick={() => update({ node: node.id, view: null, report: null, ref: null })}>
                <time>{node.analysis_date}</time><span>{t(node.research_kind === "full" ? "fullResearch" : "incrementalResearch")}</span>
                {!node.is_active && <small>{t("retainedInTrash")}</small>}
              </button>
              {comparisonMode && <button className="button compact-button" aria-pressed={selections.some(item => item.node_id === node.id)} disabled={selections.length === 2 && !selections.some(item => item.node_id === node.id)} onClick={() => toggleComparison(node)}>{t(selections.some(item => item.node_id === node.id) ? "removeFromComparison" : "selectForComparison")}</button>}
              <ActionMenu label={t("manageResearch")}>
                {node.is_active ? <>
                  {node.research_kind === "full" && !cycle.is_primary && <button className="button" disabled={busy} onClick={() => void mutate(() => api.selectPrimaryCycle(instrument, node.id))}>{t("makePrimary")}</button>}
                  <button className="button danger" disabled={busy} onClick={() => { setPendingNode(node); setLifecycleMode("trash"); setReplacementPrimary(""); }}>{t(node.research_kind === "full" ? "moveCycleToTrash" : "moveNodeToTrash")}</button>
                </> : <>
                  <button className="button" disabled={busy} onClick={() => void mutate(() => api.restoreRuns([node.id]))}>{t("restoreResearchNode")}</button>
                  <button className="button danger" disabled={busy} onClick={() => { setPendingNode(node); setLifecycleMode("purge"); }}>{t("purgeResearchNode")}</button>
                </>}
              </ActionMenu>
            </div>)}
          </details>)}
        </nav>
        {detail && (detail.timeline.cycle_total ?? 0) > CYCLE_PAGE_SIZE && <div className="pagination">
          <button className="button" disabled={cycleOffset === 0} onClick={() => update({ cycle_offset: String(Math.max(0, cycleOffset - CYCLE_PAGE_SIZE)), node: null, view: null })}>{t("previous")}</button>
          <button className="button" disabled={cycleOffset + cycles.length >= (detail.timeline.cycle_total ?? 0)} onClick={() => update({ cycle_offset: String(cycleOffset + CYCLE_PAGE_SIZE), node: null, view: null })}>{t("next")}</button>
        </div>}
      </details>
      {selected && <Suspense fallback={<div role="status">{t("loading")}</div>}><RunDetail selectedRunId={selected.id} workspace key={selected.id} /></Suspense>}
    </div>
    {comparison && <Suspense fallback={<div role="status">{t("loading")}</div>}><NodeComparison comparison={comparison} onClose={closeComparison} /></Suspense>}
    {pendingNode && lifecycleMode && <ConfirmDialog title={t(lifecycleMode === "purge" ? "purgeResearchTitle" : pendingNode.research_kind === "full" ? "cycleTrashTitle" : "nodeTrashTitle")} confirmLabel={t(lifecycleMode === "purge" ? "confirmPurge" : "confirmTimelineTrash")} cancelLabel={t("cancel")} busy={busy} onCancel={() => setPendingNode(null)} onConfirm={() => void lifecycle()}>
      <p>{t(lifecycleMode === "purge" ? "purgeResearchImpact" : pendingNode.research_kind === "full" ? "fullOwnsCycle" : "incrementalTrashImpact")}</p>
      {lifecycleMode === "trash" && pendingNode.research_kind === "full" && pendingNode.is_primary && activeFullCycles.some(cycle => cycle.id !== pendingNode.id) && <label>{t("replacementPrimaryCycle")}<select value={replacementPrimary} onChange={event => setReplacementPrimary(event.target.value)}><option value="">{t("selectReplacementCycle")}</option>{activeFullCycles.filter(cycle => cycle.id !== pendingNode.id).map(cycle => <option value={cycle.id} key={cycle.id}>{cycle.analysis_date} · {cycle.rating}</option>)}</select></label>}
      {error && <p role="alert">{error}</p>}
    </ConfirmDialog>}
  </section>;
}
function Confidence({ value }: { value?: "low" | "medium" | "high" | null }) { const { t } = useTranslation(); return <span>{value ? researchConfidenceLabel(t, value) : t("notRecorded")}</span>; }
