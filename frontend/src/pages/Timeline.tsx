import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  api,
  type ResearchNodeComparison,
  type ResearchNodeComparisonSelection,
  type ResearchNodeView,
  type ResearchTimelinePage,
  type TimelineDetail,
} from "../api/client";
import ConfirmDialog from "../components/ConfirmDialog";
import { InstrumentIdentity } from "../components/Instruments";
import ResearchRatingBadge from "../components/ResearchRatingBadge";
import { Link, usePathname } from "../router";

const CYCLE_PAGE_SIZE = 12;

function KindBadge({ kind }: { kind: ResearchNodeView["research_kind"] }) {
  const { t } = useTranslation();
  return (
    <span className={`research-kind-badge ${kind}`}>
      {t(kind === "full" ? "fullResearch" : "incrementalResearch")}
    </span>
  );
}

function Confidence({ value }: { value?: number | null }) {
  const { t } = useTranslation();
  return (
    <span className="confidence-value">
      {value == null
        ? t("notRecorded")
        : t("confidencePercent", { value: Math.round(value * 100) })}
    </span>
  );
}

function DecisionSummary({ node }: { node: ResearchNodeView }) {
  const { t } = useTranslation();
  if (!node.decision) return <p className="muted-copy">{t("notRecorded")}</p>;
  return (
    <section className="decision-summary" aria-label={t("currentDecision")}>
      <div className="decision-summary-meta">
        <ResearchRatingBadge rating={node.decision.rating} />
        <Confidence value={node.decision.confidence} />
      </div>
      <p>{node.decision.thesis}</p>
    </section>
  );
}

function IncrementalProducts({ node }: { node: ResearchNodeView }) {
  const { t } = useTranslation();
  return (
    <div className="incremental-product-grid">
      <section>
        <h4>{t("informationAdvancement")}</h4>
        <p>{node.information_advancement?.reasons?.join(", ") || t("notRecorded")}</p>
      </section>
      <section>
        <h4>{t("researchAvailability")}</h4>
        <div className="availability-row">
          {node.research_availability?.domains?.map((domain) => (
            <span className={`availability-chip ${domain.status}`} key={domain.domain}>
              {t(`${domain.domain}Analyst`)} · {t(`availability_${domain.status}`)}
            </span>
          )) ?? <span>{t("notRecorded")}</span>}
        </div>
      </section>
      {node.reassessment && (
        <details>
          <summary>{t("reassessment")}</summary>
          <ul className="compact-list">
            {node.reassessment.entries.map((entry) => (
              <li key={entry.component_id}>
                <strong>{entry.component_id}</strong>
                <span>{t(`reassessment_${entry.disposition}`)}</span>
                <p>{entry.reason}</p>
              </li>
            ))}
          </ul>
        </details>
      )}
      {node.performance && (
        <details>
          <summary>{t("performance")}</summary>
          <dl className="definition-list compact-definition-list">
            <div>
              <dt>{t("stockReturn")}</dt>
              <dd>
                {t(`performance_${node.performance.stock.status}`)}
                {node.performance.stock.calculation
                  ? ` · ${formatPercent(node.performance.stock.calculation.unrounded_return)}`
                  : node.performance.stock.reason
                    ? ` · ${node.performance.stock.reason}`
                    : ""}
              </dd>
            </div>
            {(node.performance.benchmarks ?? []).map((benchmark) => (
              <div key={benchmark.name}>
                <dt>{benchmark.name}</dt>
                <dd>
                  {t(`performance_${benchmark.component.status}`)}
                  {benchmark.component.calculation
                    ? ` · ${formatPercent(benchmark.component.calculation.unrounded_return)}`
                    : ""}
                </dd>
              </div>
            ))}
          </dl>
        </details>
      )}
      {(node.full_research_required_reasons?.length ?? 0) > 0 && (
        <section className="research-warning-block" role="status">
          <h4>{t("fullResearchRecommended")}</h4>
          {node.full_research_required_reasons?.map((reason) => (
            <p key={reason.code}>{reason.message}</p>
          ))}
        </section>
      )}
    </div>
  );
}

function NodeCard({
  node,
  selected,
  comparisonFull,
  onToggleComparison,
  onLifecycle,
  onReload,
}: {
  node: ResearchNodeView;
  selected: boolean;
  comparisonFull: boolean;
  onToggleComparison: (node: ResearchNodeView) => void;
  onLifecycle: (node: ResearchNodeView, mode: "trash" | "purge") => void;
  onReload: () => Promise<void>;
}) {
  const { t } = useTranslation();
  return (
    <article className={`research-node-card ${node.research_kind} ${node.is_active ? "" : "trashed"}`}>
      <header className="research-node-header">
        <div>
          <KindBadge kind={node.research_kind} />
          <h3>{node.analysis_date}</h3>
        </div>
        <div className="status-cluster">
          {node.is_cycle_head && <span className="status-pill">{t("cycleHead")}</span>}
          {!node.is_active && <span className="status-pill muted">{t("retainedInTrash")}</span>}
        </div>
      </header>
      <DecisionSummary node={node} />
      {node.research_kind === "incremental" && <IncrementalProducts node={node} />}
      <details className="audit-disclosure">
        <summary>{t("auditDetails")}</summary>
        <dl className="definition-list compact-definition-list">
          <div><dt>{t("informationCutoff")}</dt><dd>{new Date(node.information_cutoff_at).toLocaleString()}</dd></div>
          <div><dt>{t("researchSchema")}</dt><dd>{node.research_schema_version}</dd></div>
          <div><dt>{t("methodProvider")}</dt><dd>{String(node.method_snapshot.llm_provider ?? t("notRecorded"))}</dd></div>
          <div><dt>{t("runId")}</dt><dd><code>{node.id}</code></dd></div>
        </dl>
        <details>
          <summary>{t("methodSnapshot")}</summary>
          <pre>{JSON.stringify(node.method_snapshot, null, 2)}</pre>
        </details>
      </details>
      <footer className="node-actions">
        <button
          type="button"
          className={`button compact-button ${selected ? "selected" : ""}`}
          disabled={comparisonFull && !selected}
          aria-pressed={selected}
          onClick={() => onToggleComparison(node)}
        >
          {t(selected ? "removeFromComparison" : "selectForComparison")}
        </button>
        {node.is_active ? (
          <button
            type="button"
            className="button compact-button danger"
            onClick={() => onLifecycle(node, "trash")}
          >
            {t(node.research_kind === "full" ? "moveCycleToTrash" : "moveNodeToTrash")}
          </button>
        ) : (
          <>
            <button
              type="button"
              className="button compact-button"
              onClick={() => void api.restoreRuns([node.id]).then(onReload)}
            >
              {t("restoreResearchNode")}
            </button>
            <button
              type="button"
              className="button compact-button danger"
              onClick={() => onLifecycle(node, "purge")}
            >
              {t("purgeResearchNode")}
            </button>
          </>
        )}
        <Link className="text-link" to={`/runs/${encodeURIComponent(node.id)}`}>
          {t("openResearchDetail")} →
        </Link>
      </footer>
    </article>
  );
}

function ComparisonValue({ value }: { value: unknown }) {
  const { t } = useTranslation();
  if (value === null || value === undefined || value === "") {
    return <span className="muted-copy">{t("notApplicable")}</span>;
  }
  if (typeof value === "string" || typeof value === "number") return <span>{value}</span>;
  return <span>{JSON.stringify(value)}</span>;
}

function NodeComparisonView({ comparison }: { comparison: ResearchNodeComparison }) {
  const { t } = useTranslation();
  const [left, right] = comparison.sides;
  const reassessment = (side: (typeof comparison.sides)[number]) =>
    side.reassessment?.entries
      .map((entry) => `${entry.component_id}: ${t(`reassessment_${entry.disposition}`)}`)
      .join(", ");
  const performance = (side: (typeof comparison.sides)[number]) => {
    const stock = side.performance?.stock;
    if (!stock) return null;
    return stock.calculation
      ? `${t("stockReturn")}: ${formatPercent(stock.calculation.unrounded_return)}`
      : `${t("stockReturn")}: ${t(`performance_${stock.status}`)}${stock.reason ? ` · ${stock.reason}` : ""}`;
  };
  const method = (side: (typeof comparison.sides)[number]) =>
    [side.method_snapshot.llm_provider, side.method_snapshot.deep_model]
      .filter(Boolean)
      .join(" / ") || null;
  const rows: [string, unknown, unknown][] = [
    [t("researchRating"), left.decision?.rating, right.decision?.rating],
    [t("confidence"), left.decision?.confidence, right.decision?.confidence],
    [t("thesis"), left.decision?.thesis, right.decision?.thesis],
    [t("informationAdvancement"), left.information_advancement?.reasons?.join(", "), right.information_advancement?.reasons?.join(", ")],
    [t("researchAvailability"), left.research_availability?.domains?.map((item) => `${t(`${item.domain}Analyst`)}: ${t(`availability_${item.status}`)}`).join(", "), right.research_availability?.domains?.map((item) => `${t(`${item.domain}Analyst`)}: ${t(`availability_${item.status}`)}`).join(", ")],
    [t("reassessment"), reassessment(left), reassessment(right)],
    [t("performance"), performance(left), performance(right)],
    [t("method"), method(left), method(right)],
  ];
  return (
    <section className="panel comparison-panel" aria-label={t("nodeComparison")}>
      <div className="panel-header">
        <div><p className="eyebrow">{t("nodeComparison")}</p><h2>{t("selectedResearchNodes")}</h2></div>
        <span>{t(comparison.cross_cycle ? "crossCycleComparison" : "sameCycleComparison")}</span>
      </div>
      {comparison.method_changed && <div className="notice" role="status">{t("methodChanged")}</div>}
      <div className="table-wrap comparison-decision-table">
        <table>
          <thead><tr><th>{t("decisionSection")}</th>{[left, right].map((side) => <th key={side.node_id}><KindBadge kind={side.research_kind} /> {side.analysis_date}</th>)}</tr></thead>
          <tbody>{rows.map(([label, leftValue, rightValue]) => <tr key={label}><th scope="row">{label}</th><td><ComparisonValue value={leftValue} /></td><td><ComparisonValue value={rightValue} /></td></tr>)}</tbody>
        </table>
      </div>
      <details className="audit-disclosure">
        <summary>{t("auditDetails")}</summary>
        <div className="comparison-grid">{comparison.sides.map((side) => <pre key={side.node_id}>{JSON.stringify(side, null, 2)}</pre>)}</div>
      </details>
    </section>
  );
}

export default function Timeline() {
  const { t } = useTranslation();
  const pathname = usePathname();
  const isList = pathname === "/timelines";
  const instrument = decodeURIComponent(pathname.split("/").at(-1) ?? "");
  const [detail, setDetail] = useState<TimelineDetail | null>(null);
  const [timelines, setTimelines] = useState<ResearchTimelinePage | null>(null);
  const [listOffset, setListOffset] = useState(0);
  const [cycleOffset, setCycleOffset] = useState(0);
  const [query, setQuery] = useState("");
  const [showRetainedTrash, setShowRetainedTrash] = useState(false);
  const [comparisonNodes, setComparisonNodes] = useState<ResearchNodeView[]>([]);
  const [comparison, setComparison] = useState<ResearchNodeComparison | null>(null);
  const [comparisonBusy, setComparisonBusy] = useState(false);
  const [pendingNode, setPendingNode] = useState<ResearchNodeView | null>(null);
  const [lifecycleMode, setLifecycleMode] = useState<"trash" | "purge" | null>(null);
  const [replacementPrimary, setReplacementPrimary] = useState("");
  const [lifecycleBusy, setLifecycleBusy] = useState(false);
  const [error, setError] = useState("");

  const cycles = detail?.timeline.cycles ?? [];
  const cycleTotal = detail?.timeline.cycle_total ?? 0;
  const cycleLimit = detail?.timeline.cycle_limit ?? CYCLE_PAGE_SIZE;
  const activeFullCycles = cycles.filter((cycle) => cycle.baseline.is_active);
  const filteredTimelines = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    if (!normalized) return timelines?.items ?? [];
    return (timelines?.items ?? []).filter((item) =>
      [item.instrument, item.instrument_name, item.instrument_local_name]
        .filter(Boolean)
        .some((value) => String(value).toLocaleLowerCase().includes(normalized)),
    );
  }, [query, timelines]);

  useEffect(() => {
    let active = true;
    setError("");
    const fail = (cause: unknown) => {
      if (active) setError(cause instanceof Error ? cause.message : t("timelineLoadFailed"));
    };
    if (isList) {
      void api.timelines(50, listOffset).then((value) => active && setTimelines(value), fail);
    } else {
      void api.timeline(instrument, CYCLE_PAGE_SIZE, cycleOffset, showRetainedTrash ? "all" : "active").then((value) => active && setDetail(value), fail);
    }
    return () => { active = false; };
  }, [cycleOffset, instrument, isList, listOffset, showRetainedTrash, t]);

  useEffect(() => {
    setCycleOffset(0);
    setComparisonNodes([]);
    setComparison(null);
  }, [instrument]);

  const reloadDetail = async () => {
    setDetail(await api.timeline(instrument, CYCLE_PAGE_SIZE, cycleOffset, showRetainedTrash ? "all" : "active"));
  };

  const toggleComparison = (node: ResearchNodeView) => {
    setComparison(null);
    setComparisonNodes((current) => {
      if (current.some((item) => item.id === node.id)) return current.filter((item) => item.id !== node.id);
      return current.length < 2 ? [...current, node] : current;
    });
  };

  const compareSelected = async () => {
    if (comparisonNodes.length !== 2) return;
    const selections: ResearchNodeComparisonSelection[] = comparisonNodes.map((node) => ({ node_id: node.id, lifecycle_state: node.is_active ? "active" : "trashed" }));
    setComparisonBusy(true);
    try {
      setComparison(await api.compareResearchNodes(instrument, selections));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("timelineLoadFailed"));
    } finally {
      setComparisonBusy(false);
    }
  };

  const applyLifecycle = async () => {
    if (!pendingNode || !lifecycleMode) return;
    if (lifecycleMode === "trash" && pendingNode.research_kind === "full" && pendingNode.is_primary && activeFullCycles.some((cycle) => cycle.id !== pendingNode.id) && !replacementPrimary) {
      setError(t("selectReplacementCycle"));
      return;
    }
    setLifecycleBusy(true);
    try {
      if (lifecycleMode === "trash") await api.trashRuns([pendingNode.id], replacementPrimary ? { [pendingNode.id]: replacementPrimary } : {});
      else await api.purgeRuns([pendingNode.id]);
      setPendingNode(null);
      setLifecycleMode(null);
      setReplacementPrimary("");
      await reloadDetail();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("timelineLoadFailed"));
    } finally {
      setLifecycleBusy(false);
    }
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

  return (
    <section>
      <header className="page-header research-header">
        <div><p className="eyebrow">{t("researchTimeline")}</p><InstrumentIdentity ticker={instrument} instrumentName={detail?.timeline.instrument_name} instrumentLocalName={detail?.timeline.instrument_local_name} prominent /></div>
        <div className="action-row"><Link className="button" to="/timelines">{t("allResearchTimelines")}</Link><button className="button" onClick={() => { setCycleOffset(0); setShowRetainedTrash((value) => !value); }}>{t(showRetainedTrash ? "hideRetainedTrash" : "showRetainedTrash")}</button></div>
      </header>
      {error && <div className="alert">{error}</div>}
      {!detail && !error && <div className="loading">{t("loading")}</div>}
      {detail?.timeline.timeline_warning && <div className="alert">{t("fullResearchRecommended")}</div>}
      {detail && cycles.length === 0 && <div className="empty-state">{t("noCommittedFullResearch")}</div>}
      {detail && (
        <aside className="comparison-tray" aria-label={t("comparisonSelection")}>
          <div><strong>{t("comparisonSelection")}</strong><span>{t("comparisonSelectionHint")}</span></div>
          <div className="comparison-selections">{comparisonNodes.map((node) => <button type="button" onClick={() => toggleComparison(node)} key={node.id}><KindBadge kind={node.research_kind} /> {node.analysis_date} · {node.decision?.rating ?? t("notRecorded")} ×</button>)}</div>
          <button className="button primary" disabled={comparisonNodes.length !== 2 || comparisonBusy} onClick={() => void compareSelected()}>{t("compareSelectedNodes")}</button>
        </aside>
      )}
      {comparison && <NodeComparisonView comparison={comparison} />}
      <div className="research-cycle-list">
        {cycles.map((cycle) => (
          <section className={`research-cycle ${cycle.is_primary ? "primary" : ""}`} key={cycle.id}>
            <header className="research-cycle-header">
              <div><p className="eyebrow">{t("researchCycle")}</p><h2>{cycle.baseline.analysis_date}</h2></div>
              <div className="status-cluster">{cycle.is_primary && <span className="status-pill primary">{t("primaryCycle")}</span>}{cycle.cycle_warning && <span className="status-pill warning">{t("fullResearchRecommended")}</span>}{!cycle.is_primary && cycle.baseline.is_active && <button className="button compact-button" onClick={() => void api.selectPrimaryCycle(instrument, cycle.id).then(setDetail)}>{t("makePrimary")}</button>}</div>
            </header>
            <div className="cycle-rail">
              <NodeCard node={cycle.baseline} selected={comparisonNodes.some((item) => item.id === cycle.baseline.id)} comparisonFull={comparisonNodes.length === 2} onToggleComparison={toggleComparison} onLifecycle={(node, mode) => { setPendingNode(node); setLifecycleMode(mode); setReplacementPrimary(""); }} onReload={reloadDetail} />
              {(cycle.increments ?? []).map((node) => <NodeCard node={node} selected={comparisonNodes.some((item) => item.id === node.id)} comparisonFull={comparisonNodes.length === 2} onToggleComparison={toggleComparison} onLifecycle={(target, mode) => { setPendingNode(target); setLifecycleMode(mode); setReplacementPrimary(""); }} onReload={reloadDetail} key={node.id} />)}
            </div>
          </section>
        ))}
      </div>
      {detail && cycleTotal > cycleLimit && (
        <div className="pagination"><button className="button" disabled={cycleOffset === 0} onClick={() => setCycleOffset((value) => Math.max(0, value - cycleLimit))}>← {t("previous")}</button><span>{t("runRange", { start: cycleOffset + 1, end: Math.min(cycleOffset + cycles.length, cycleTotal), total: cycleTotal })}</span><button className="button" disabled={cycleOffset + cycles.length >= cycleTotal} onClick={() => setCycleOffset((value) => value + cycleLimit)}>{t("next")} →</button></div>
      )}
      {pendingNode && lifecycleMode && (
        <ConfirmDialog title={t(lifecycleMode === "purge" ? "purgeResearchTitle" : pendingNode.research_kind === "full" ? "cycleTrashTitle" : "nodeTrashTitle")} confirmLabel={t(lifecycleMode === "purge" ? "confirmPurge" : "confirmTimelineTrash")} cancelLabel={t("cancel")} busy={lifecycleBusy} onCancel={() => { setPendingNode(null); setLifecycleMode(null); }} onConfirm={() => void applyLifecycle()}>
          <p>{t(lifecycleMode === "purge" ? "purgeResearchImpact" : pendingNode.research_kind === "full" ? "fullOwnsCycle" : "incrementalTrashImpact")}</p>
          {lifecycleMode === "trash" && pendingNode.research_kind === "full" && pendingNode.is_primary && activeFullCycles.some((cycle) => cycle.id !== pendingNode.id) && (
            <label>{t("replacementPrimaryCycle")}<select value={replacementPrimary} onChange={(event) => setReplacementPrimary(event.target.value)}><option value="">{t("selectReplacementCycle")}</option>{activeFullCycles.filter((cycle) => cycle.id !== pendingNode.id).map((cycle) => <option key={cycle.id} value={cycle.id}>{cycle.baseline.analysis_date} · {cycle.baseline.decision?.rating ?? t("notRecorded")} · {cycle.baseline.decision?.confidence == null ? t("notRecorded") : `${Math.round(cycle.baseline.decision.confidence * 100)}%`}</option>)}</select></label>
          )}
        </ConfirmDialog>
      )}
    </section>
  );
}

function formatPercent(value: number) {
  return new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: 2 }).format(value);
}
