import { useTranslation } from "react-i18next";
import type { ResearchCycleView, ResearchNodeView, ResearchNodeComparisonSelection } from "../api/client";
import ResearchRatingBadge from "./ResearchRatingBadge";
import { ActionMenu } from "./Interaction";
import type { LifecycleAction } from "./RunLifecycleDialog";

export default function CycleHistory({ cycles, selectedId, selectedCycleId, comparisonMode = false, selections = [], busy = false, onSelect, onCompare, onManage, onPrimary }: {
  cycles: ResearchCycleView[]; selectedId?: string; selectedCycleId?: string;
  comparisonMode?: boolean; selections?: ResearchNodeComparisonSelection[]; busy?: boolean;
  onSelect: (id: string) => void; onCompare?: (node: ResearchNodeView) => void;
  onManage?: (node: ResearchNodeView, action: LifecycleAction) => void; onPrimary?: (id: string) => void;
}) {
  const { t } = useTranslation();
  return (
        <nav aria-label={t("historyNavigation")}>
          {cycles.map(cycle => <details className="history-cycle" open={cycle.is_primary || cycle.id === selectedCycleId || comparisonMode} key={cycle.id}>
            <summary>{cycle.baseline.analysis_date} {cycle.is_primary && <small>{t("primaryCycle")}</small>}{cycle.cycle_warning && <small className="warning">{t("fullResearchRecommended")}</small>}</summary>
            {[cycle.baseline, ...(cycle.increments ?? [])].map(node => <div className={`history-node ${node.research_kind}`} key={node.id}>
              <button className="history-select" aria-current={selectedId === node.id ? "page" : undefined} onClick={() => onSelect(node.id)}>
                <time>{node.analysis_date}</time><span>{t(node.research_kind === "full" ? "fullResearch" : "incrementalResearch")}</span>
                {node.decision && <ResearchRatingBadge rating={node.decision.rating} />}
                {node.decision_outcome && <small>{t(`decisionOutcome_${node.decision_outcome}`)}</small>}
                {node.is_cycle_head && <small className="cycle-head-label">{t("cycleHead")}</small>}
                {!node.is_active && <small>{t("retainedInTrash")}</small>}
              </button>
              {comparisonMode && <button className="button compact-button" aria-pressed={selections.some(item => item.node_id === node.id)} disabled={selections.length === 2 && !selections.some(item => item.node_id === node.id)} onClick={() => onCompare?.(node)}>{t(selections.some(item => item.node_id === node.id) ? "removeFromComparison" : "selectForComparison")}</button>}
              {onManage && <ActionMenu label={t("manageResearch")}>
                {node.is_active ? <>
                  {node.research_kind === "full" && !cycle.is_primary && <button className="button" disabled={busy} onClick={() => onPrimary?.(node.id)}>{t("makePrimary")}</button>}
                  <button className="button danger" disabled={busy} onClick={() => onManage?.(node, "trash")}>{t(node.research_kind === "full" ? "moveCycleToTrash" : "moveNodeToTrash")}</button>
                </> : <>
                  <button className="button" disabled={busy} onClick={() => onManage?.(node, "restore")}>{t("restoreResearchNode")}</button>
                  <button className="button danger" disabled={busy} onClick={() => onManage?.(node, "purge")}>{t("purgeResearchNode")}</button>
                </>}
              </ActionMenu>}
            </div>)}
          </details>)}
        </nav>
  );
}
