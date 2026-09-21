import type { RunSummaryView } from "../../shared/api/client";

export function researchLocation(run: RunSummaryView): string {
  return `/timelines/${encodeURIComponent(run.request.ticker)}?node=${encodeURIComponent(run.id)}${run.trashed_at ? "&trash_state=all" : ""}`;
}
