import ResearchRatingBadge from "./ResearchRatingBadge";
import { researchConfidenceLabel } from "../../shared/i18n";
import CycleHistory from "./CycleHistory";
import { lazy, Suspense, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type ResearchNodeComparison, type ResearchNodeComparisonSelection, type ResearchNodeView, type TimelineDetail } from "../../shared/api/client";
import ResearchWorkspace from "./ResearchWorkspace";
import RunLifecycleDialog, { type LifecycleAction } from "./RunLifecycleDialog";
import { InstrumentIdentity } from "../../shared/Instruments";
import { ActionMenu } from "../../shared/Interaction";
import { Link, useLocation, useNavigate } from "../../app/router";
const ResearchLibrary = lazy(() => import("./ResearchLibrary"));
const ResearchReader = lazy(() => import("./ResearchReader"));
const NodeComparison = lazy(() => import("./NodeComparison"));
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
  const comparisonView = params.get("view") === "compare";
  const comparisonMode = params.get("compare_mode") === "1" || params.has("compare") || comparisonView;
  const selections = params.getAll("compare").slice(0, 2).map(value => {
    const split = value.indexOf(":");
    return { node_id: value.slice(split + 1), lifecycle_state: value.slice(0, split) === "trashed" ? "trashed" : "active" } as ResearchNodeComparisonSelection;
  });
  const [actionsTarget, setActionsTarget] = useState<HTMLDivElement | null>(null);
  const [loadedDetail, setDetail] = useState<TimelineDetail | null>(null);
  const detail = loadedDetail?.timeline.instrument === instrument ? loadedDetail : null;
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [comparison, setComparison] = useState<ResearchNodeComparison | null>(null);
  const [busy, setBusy] = useState(false);
  const [pendingNode, setPendingNode] = useState<ResearchNodeView | null>(null);
  const [lifecycleMode, setLifecycleMode] = useState<LifecycleAction | null>(null);
  const update = (values: Record<string, string | null>, replace = false) => {
    const next = new URLSearchParams(params);
    Object.entries(values).forEach(([key, value]) => { if (value === null) next.delete(key); else next.set(key, value); });
    navigate(`${location.pathname}${next.size ? `?${next}` : ""}`, { replace });
  };
  useEffect(() => {
    let active = true;
    setError(""); setDetail(null);
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
  const closeComparison = () => {
    if (location.returnResearch) navigate(location.returnResearch.url);
    else update({ view: null, compare_mode: null, compare: null, changed_only: null });
  };
  const toggleComparison = (node: ResearchNodeView) => {
    const next = new URLSearchParams(params);
    next.delete("compare");
    const remaining = selections.some(item => item.node_id === node.id)
      ? selections.filter(item => item.node_id !== node.id)
      : [...selections, { node_id: node.id, lifecycle_state: node.is_active ? "active" : "trashed" }].slice(0, 2);
    remaining.forEach(item => next.append("compare", `${item.lifecycle_state}:${item.node_id}`));
    navigate(`${location.pathname}?${next}`, { replace: true }); setComparison(null);
  };
  const compare = () => { if (selections.length === 2 && !busy) update({ view: "compare" }); };
  useEffect(() => {
    if (!comparisonView || selections.length !== 2) return;
    let active = true;
    setBusy(true); setError(""); setComparison(null);
    void api.compareResearchNodes(instrument, selections).then(value => {
      if (active) setComparison(value);
    }, cause => { if (active) setError(cause instanceof Error ? cause.message : String(cause)); })
      .finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [comparisonView, selectionKey, revision]);
  const swapComparison = () => {
    const next = new URLSearchParams(params); next.delete("compare");
    [...selections].reverse().forEach(item => next.append("compare", `${item.lifecycle_state}:${item.node_id}`));
    navigate(`${location.pathname}?${next}`, { replace: true });
  };
  const mutate = async (action: () => Promise<unknown>) => {
    setBusy(true); setError("");
    try { await action(); setRevision(value => value + 1); }
    catch (cause) { if (currentInstrument.current === instrument) setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { if (currentInstrument.current === instrument) setBusy(false); }
  };
  if (isList) return <Suspense fallback={<div className="loading">{t("loading")}</div>}><ResearchLibrary /></Suspense>;
  const secondaryActions = <>
    <button className="button" onClick={() => comparisonView ? closeComparison() : update({ compare_mode: comparisonMode ? null : "1", compare: null })}>{t(comparisonMode ? "closeComparisonMode" : "comparisonMode")}</button>
    <button className="button" onClick={() => update({ trash_state: showRetainedTrash ? null : "all", compare: null, cycle_offset: null })}>{t(showRetainedTrash ? "hideRetainedTrash" : "showRetainedTrash")}</button>
  </>;
  return <section className="timeline-page">
    <header className="page-header research-header">
      <div><Link className="back-link" to={location.sourceLibrary?.url ?? "/timelines"}>{t("backToResearch")}</Link>
        <InstrumentIdentity ticker={instrument} instrumentName={detail?.timeline.instrument_name} instrumentLocalName={detail?.timeline.instrument_local_name} prominent />
        {selected?.decision && <div className="workspace-judgment"><ResearchRatingBadge rating={selected.decision.rating} /><span>{researchConfidenceLabel(t, selected.decision.confidence)}</span></div>}
        {selected && <div className="workspace-context"><span>{t("selectedCutoff")}: {selected.analysis_date}</span><span>{t("baselineDate")}: {selectedCycle?.baseline.analysis_date}</span>
          <span>{t(selectedCycle?.is_primary && selected.is_cycle_head ? "currentPrimary" : "selectedResearch")}</span></div>}
      </div>
      <div className="workspace-actions-container">
        <div className="workspace-header-actions" ref={setActionsTarget} />
        <ActionMenu label={t("moreResearchActions")}>
          {secondaryActions}
          {selected && <>
            <Link className="button" to={`/runs/new?intent=clone_full&from_run=${encodeURIComponent(selected.id)}`}>{t("cloneAsFullResearch")}</Link>
            <Link className="button" to={`/runs/${encodeURIComponent(selected.id)}?view=diagnostics`}>{t("runDiagnostics")}</Link>
          </>}
        </ActionMenu>
      </div>
    </header>
    {error && <div className="alert" role="alert">{error}<button className="button" onClick={() => setRevision(value => value + 1)}>{t("retryLoad")}</button></div>}
    {!detail && !error && <div className="loading" role="status">{t("loading")}</div>}
    {detail?.timeline.timeline_warning && <p className="research-limitations" role="status">{t("fullResearchRecommended")}</p>}
    {detail && !selected && <div className="empty-state">{t(requestedNode ? "unavailableResearch" : "noCommittedFullResearch")}</div>}
    {comparisonView && selections.length !== 2 && <p className="alert">{t("comparisonSelectionHint")}</p>}
    {comparisonMode && !comparisonView && <aside className="comparison-tray" aria-label={t("comparisonSelection")}>
      <span>{t("comparisonSelectionHint")}</span>
      {selections.map((item, index) => <span className="comparison-selection-chip" key={item.node_id}>
        {nodes.find(node => node.id === item.node_id)?.analysis_date ?? `${t("selectedResearch")} ${index + 1}`}
        <button aria-label={t("removeFromComparison")} onClick={() => {
          const next = new URLSearchParams(params); next.delete("compare"); selections.filter(other => other.node_id !== item.node_id).forEach(other => next.append("compare", `${other.lifecycle_state}:${other.node_id}`)); navigate(`${location.pathname}?${next}`, { replace: true });
        }}>×</button></span>)}
      <button className="button primary" disabled={selections.length !== 2 || busy} onClick={() => void compare()}>{t("compareSelectedNodes")}</button>
    </aside>}
    <ResearchWorkspace history={<div className="workspace-history">
        <CycleHistory cycles={cycles} selectedId={selected?.id} selectedCycleId={selected?.cycle_id} comparisonMode={comparisonMode} selections={selections} busy={busy} onSelect={id => update({ node: id, view: null, report: null, ref: null })} onCompare={toggleComparison} onManage={(node, action) => { setPendingNode(node); setLifecycleMode(action); }} onPrimary={id => void mutate(() => api.selectPrimaryCycle(instrument, id))} />
        {detail && (detail.timeline.cycle_total ?? 0) > CYCLE_PAGE_SIZE && <div className="pagination">
          <button className="button" disabled={cycleOffset === 0} onClick={() => update({ cycle_offset: String(Math.max(0, cycleOffset - CYCLE_PAGE_SIZE)), node: null, view: null })}>{t("previous")}</button>
          <button className="button" disabled={cycleOffset + cycles.length >= (detail.timeline.cycle_total ?? 0)} onClick={() => update({ cycle_offset: String(cycleOffset + CYCLE_PAGE_SIZE), node: null, view: null })}>{t("next")}</button>
        </div>}
      </div>}>
      {selected && !comparisonView && <Suspense fallback={<div role="status">{t("loading")}</div>}><ResearchReader selectedRunId={selected.id} workspace actionsTarget={actionsTarget} key={selected.id} /></Suspense>}
      {comparisonView && busy && <p role="status">{t("loading")}</p>}
      {comparisonView && comparison && <Suspense fallback={<div role="status">{t("loading")}</div>}><NodeComparison comparison={comparison} baselineDates={rememberedCycles.current.dates} primaryCycleId={detail?.timeline.primary_cycle_id} changedOnly={params.get("changed_only") !== "0"} onChangedOnly={value => update({ changed_only: value ? "1" : "0" }, true)} onSwap={swapComparison} onClose={closeComparison} /></Suspense>}
    </ResearchWorkspace>
    {pendingNode && lifecycleMode && <RunLifecycleDialog runIds={[pendingNode.id]} action={lifecycleMode} onClose={() => setPendingNode(null)} onDone={() => { setPendingNode(null); setRevision(value => value + 1); }} />}
  </section>;
}
