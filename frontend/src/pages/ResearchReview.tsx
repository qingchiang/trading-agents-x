import { ResearchReviewView } from "./research-review/ResearchReviewView";
import { useResearchReviewPage } from "./research-review/useResearchReviewPage";

export default function ResearchReview() {
  const page = useResearchReviewPage();
  return <ResearchReviewView {...page} />;
}
