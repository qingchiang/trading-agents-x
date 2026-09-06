import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type ResearchNodeComparison, type ResearchNodeComparisonSelection, type ResearchNodeView, type TimelineDetail } from "../api/client";
import ConfirmDialog from "../components/ConfirmDialog";
import { InstrumentIdentity } from "../components/Instruments";
import { ActionMenu } from "../components/Interaction";
import { Link, useLocation, useNavigate } from "../router";
const ResearchLibrary = lazy(() => import("./ResearchLibrary"));
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
  const currentInstrument = useRef(instrument);
  currentInstrument.current = instrument;
  const rememberedCycles = useRef<{ instrument: string; dates: Record<string, string> }>({ instrument, dates: {} });
  if (rememberedCycles.current.instrument !== instrument) rememberedCycles.current = { instrument, dates: {} };
  const requestedNode = params.get("node") ?? "";
  const showRetainedTrash = params.get("trash_state") === "all";
  const cycleOffset = Math.max(0, Number(params.get("cycle_offset")) || 0);
  const comparisonMode = params.get("compare_mode") === "1";
  const selections = params.getAll("compare").slice(0, 2).map(value => {
    const split = value.indexOf(":");
    return { node_id: value.slice(split + 1), lifecycle_state: value.slice(0, split) === "trashed" ? "trashed" : "active" } as ResearchNodeComparisonSelection;
  });
  const [loadedDetail, setDetail] = useState<TimelineDetail | null>(null);
  const detail = loadedDetail?.timeline.instrument === instrument ? loadedDetail : null;
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
  useEffect(() => {
    let active = true;
    setError(""); setDetail(null); setComparison(null); setBusy(false);
    if (!isList) {
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
  }, [isList, instrument, cycleOffset, requestedNode, showRetainedTrash, revision]);
  const cycles = detail?.timeline.cycles ?? [];
  const nodes = cycles.flatMap(cycle => [cycle.baseline, ...(cycle.increments ?? [])]);
  const primaryCycle = cycles.find(cycle => cycle.is_primary);
  const selected = requestedNode ? nodes.find(node => node.id === requestedNode)
    : nodes.find(node => node.id === primaryCycle?.head_run_id) ?? nodes.find(node => node.id === cycles[0]?.head_run_id);
  const selectedCycle = cycles.find(cycle => cycle.id === selected?.cycle_id);
  const activeFullCycles = detail?.timeline.active_full_cycles ?? [];
  for (const cycle of cycles) rememberedCycles.current.dates[cycle.id] = cycle.baseline.analysis_date;
  for (const cycle of activeFullCycles) rememberedCycles.current.dates[cycle.id] = cycle.analysis_date;
  const selectionKey = `${instrument}:${params.getAll("compare").join("|")}`;
  const currentSelection = useRef(selectionKey);
  currentSelection.current = selectionKey;
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
    try { const value = await api.compareResearchNodes(instrument, selections); if (currentSelection.current === selectionKey) setComparison(value); }
    catch (cause) { if (currentInstrument.current === instrument) setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { if (currentInstrument.current === instrument) setBusy(false); }
  };
  const lifecycle = async () => {
    if (!pendingNode || !lifecycleMode || busy) return;
    if (lifecycleMode === "trash" && pendingNode.research_kind === "full" && pendingNode.is_primary && activeFullCycles.some(c => c.id !== pendingNode.id) && !replacementPrimary) { setError(t("selectReplacementCycle")); return; }
    setBusy(true);
    try {
      if (lifecycleMode === "purge") await api.purgeRuns([pendingNode.id]);
      else await api.trashRuns([pendingNode.id], replacementPrimary ? { [pendingNode.id]: replacementPrimary } : {});
      setPendingNode(null); setRevision(value => value + 1);
    } catch (cause) { if (currentInstrument.current === instrument) setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { if (currentInstrument.current === instrument) setBusy(false); }
  };
  const mutate = async (action: () => Promise<unknown>) => {
    setBusy(true); setError("");
    try { await action(); setRevision(value => value + 1); }
    catch (cause) { if (currentInstrument.current === instrument) setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { if (currentInstrument.current === instrument) setBusy(false); }
  };
  if (isList) return <Suspense fallback={<div className="loading">{t("loading")}</div>}><ResearchLibrary /></Suspense>;
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
      <details className="workspace-history" open={window.innerWidth > 1024 || undefined}><summary>{t("historyNavigation")}</summary>
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
    {comparison && <Suspense fallback={<div role="status">{t("loading")}</div>}><NodeComparison comparison={comparison} baselineDates={rememberedCycles.current.dates} onClose={closeComparison} /></Suspense>}
    {pendingNode && lifecycleMode && <ConfirmDialog title={t(lifecycleMode === "purge" ? "purgeResearchTitle" : pendingNode.research_kind === "full" ? "cycleTrashTitle" : "nodeTrashTitle")} confirmLabel={t(lifecycleMode === "purge" ? "confirmPurge" : "confirmTimelineTrash")} cancelLabel={t("cancel")} busy={busy} onCancel={() => setPendingNode(null)} onConfirm={() => void lifecycle()}>
      <p>{t(lifecycleMode === "purge" ? "purgeResearchImpact" : pendingNode.research_kind === "full" ? "fullOwnsCycle" : "incrementalTrashImpact")}</p>
      {lifecycleMode === "trash" && pendingNode.research_kind === "full" && pendingNode.is_primary && activeFullCycles.some(cycle => cycle.id !== pendingNode.id) && <label>{t("replacementPrimaryCycle")}<select value={replacementPrimary} onChange={event => setReplacementPrimary(event.target.value)}><option value="">{t("selectReplacementCycle")}</option>{activeFullCycles.filter(cycle => cycle.id !== pendingNode.id).map(cycle => <option value={cycle.id} key={cycle.id}>{cycle.analysis_date} · {cycle.rating}</option>)}</select></label>}
      {error && <p role="alert">{error}</p>}
    </ConfirmDialog>}
  </section>;
}
