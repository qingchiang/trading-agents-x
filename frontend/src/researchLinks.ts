import type { RunSummaryView } from "./api/client";

export const readingViews = new Set(["decision", "reports", "brief", "incremental", "reassessment", "evidence", "deliberation"]);

export function researchLocation(run: RunSummaryView): string {
  return run.is_research_node
    ? `/timelines/${encodeURIComponent(run.request.ticker)}?node=${encodeURIComponent(run.id)}${run.trashed_at ? "&trash_state=all" : ""}`
    : `/runs/${encodeURIComponent(run.id)}?view=${run.research_kind === "incremental" ? "brief" : "decision"}`;
}

export function legacyResearchLocation(run: RunSummaryView, search: string, hash: string): string | null {
  const params = new URLSearchParams(search);
  const view = params.get("view") ?? "";
  if (!run.is_research_node || !readingViews.has(view)) return null;
  if (view === "incremental") params.set("view", "brief");
  params.set("node", run.id);
  if (run.trashed_at && !params.has("trash_state")) params.set("trash_state", "all");
  return `/timelines/${encodeURIComponent(run.request.ticker)}?${params}${hash}`;
}
