import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ResearchCycleView, ResearchNodeView, ResearchNodeComparisonSelection } from "../../shared/api/client";
import ResearchRatingBadge from "./ResearchRatingBadge";
import { ActionMenu } from "../../shared/Interaction";
import type { LifecycleAction } from "./RunLifecycleDialog";

export default function CycleHistory({ cycles, selectedId, selectedCycleId, comparisonMode = false, selections = [], busy = false, onSelect, onCompare, onManage, onPrimary }: {
  cycles: ResearchCycleView[]; selectedId?: string; selectedCycleId?: string;
  comparisonMode?: boolean; selections?: ResearchNodeComparisonSelection[]; busy?: boolean;
  onSelect: (id: string) => void; onCompare?: (node: ResearchNodeView) => void;
  onManage?: (node: ResearchNodeView, action: LifecycleAction) => void; onPrimary?: (id: string) => void;
}) {
  const { t } = useTranslation();
  const prefix = useId();
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  useEffect(() => {
    if (selectedCycleId) setExpanded(values => ({ ...values, [selectedCycleId]: true }));
  }, [selectedCycleId, selectedId]);
  return (
        <nav aria-label={t("historyNavigation")}>
          {cycles.map(cycle => {
            const open = comparisonMode || (expanded[cycle.id] ?? (cycle.is_primary || cycle.id === selectedCycleId));
            return <section className="history-cycle" key={cycle.id}>
            <header className="history-cycle-heading">
              <h3>{t("researchCycle")} · <time>{cycle.baseline.analysis_date}</time></h3>
              {cycle.is_primary && <small>{t("primaryCycle")}</small>}{cycle.cycle_warning && <small className="warning">{t("fullResearchRecommended")}</small>}
              {!comparisonMode && <button className="text-button" aria-expanded={open} aria-controls={`${prefix}-${cycle.id}`} aria-label={`${t(open ? "collapseCycle" : "expandCycle")} · ${cycle.baseline.analysis_date}`} onClick={() => setExpanded(values => ({ ...values, [cycle.id]: !open }))}>{t(open ? "collapseCycle" : "expandCycle")}</button>}
            </header>
            <div id={`${prefix}-${cycle.id}`} hidden={!open}>
            {[cycle.baseline, ...(cycle.increments ?? [])].map(node => <div className={`history-node ${node.research_kind}`} key={node.id}>
              <button className="history-select" aria-current={selectedId === node.id ? "page" : undefined} onClick={() => onSelect(node.id)}>
                <time>{node.analysis_date}</time><span className="history-node-context"><span>{t(node.research_kind === "full" ? "fullBaseline" : "incrementalResearch")}</span>
                {node.decision && <ResearchRatingBadge rating={node.decision.rating} />}</span>
                {!node.decision && node.decision_outcome && <small>{t(`decisionOutcome_${node.decision_outcome}`)}</small>}
                {node.is_cycle_head && <small className="cycle-head-label">{t("cycleHead")}</small>}
                {!node.is_active && <small>{t("retainedInTrash")}</small>}
              </button>
              {comparisonMode && <label className="comparison-choice"><input type="checkbox" aria-label={`${t(selections.some(item => item.node_id === node.id) ? "removeFromComparison" : "selectForComparison")} · ${node.analysis_date}`} checked={selections.some(item => item.node_id === node.id)} disabled={selections.length === 2 && !selections.some(item => item.node_id === node.id)} onChange={() => onCompare?.(node)} /><span>{t(selections.some(item => item.node_id === node.id) ? "comparisonSelected" : "comparisonAdd")}</span></label>}
              {onManage && !comparisonMode && <ActionMenu label={t("manageResearch")}>
                {node.is_active ? <>
                  {node.research_kind === "full" && !cycle.is_primary && <button className="button" disabled={busy} onClick={() => onPrimary?.(node.id)}>{t("makePrimary")}</button>}
                  <button className="button danger" disabled={busy} onClick={() => onManage?.(node, "trash")}>{t(node.research_kind === "full" ? "moveCycleToTrash" : "moveNodeToTrash")}</button>
                </> : <>
                  <button className="button" disabled={busy} onClick={() => onManage?.(node, "restore")}>{t("restoreResearchNode")}</button>
                  <button className="button danger" disabled={busy} onClick={() => onManage?.(node, "purge")}>{t("purgeResearchNode")}</button>
                </>}
              </ActionMenu>}
            </div>)}
          </div></section>; })}
          {comparisonMode && selections.length === 2 && <p className="secondary-line" role="status">{t("comparisonLimit")}</p>}
        </nav>
  );
}
