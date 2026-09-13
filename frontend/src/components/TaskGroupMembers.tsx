import { useTranslation } from "react-i18next";
import type { ReactNode } from "react";
import type { RunGroupPage, RunSummaryView } from "../api/client";

type Group = RunGroupPage["items"][number];

/** Keep current activity visible while retaining the entire cycle's history. */
export default function TaskGroupMembers({ group, filtered, renderRow }: {
  group: Group;
  filtered: boolean;
  renderRow: (run: RunSummaryView, matched: boolean, baseline?: boolean) => ReactNode;
}) {
  const { t } = useTranslation();
  const runs = group.research_runs ?? [];
  const latest = runs.filter(run => !run.trashed_at).reduce<RunSummaryView | undefined>((head, run) =>
    !head || run.request.analysis_date > head.request.analysis_date ? run : head, undefined);
  const matching = new Set(group.matched_run_ids ?? []);
  const prominent = (run: RunSummaryView) => run.id === group.baseline?.id || run.id === latest?.id
    || ["queued", "running", "failed"].includes(run.status) || filtered && matching.has(run.id);
  const history = runs.filter(run => !prominent(run));
  const row = (run: RunSummaryView) => renderRow(run, matching.has(run.id), run.id === group.baseline?.id);
  return <>
    {runs.filter(prominent).map(row)}
    {(group.related_tasks ?? []).length > 0 && <div className={group.baseline ? "related-tasks" : "standalone-tasks"}>
      {group.baseline && <p>{t("relatedTasksHint")}</p>}
      {(group.related_tasks ?? []).map(run => renderRow(run, matching.has(run.id)))}
    </div>}
    {history.length > 0 && <details className="task-history">
      <summary>{t("historicalTasks", { count: history.length })}</summary>
      {history.map(row)}
    </details>}
  </>;
}
