import type { RunSummaryView } from "../api/client";

export default function ResearchRatingBadge({
  rating,
}: {
  rating?: RunSummaryView["research_rating"];
}) {
  const tone = rating?.toLowerCase() ?? "unavailable";
  return (
    <span className={`research-rating-badge rating-${tone}`}>
      {rating ?? "—"}
    </span>
  );
}
