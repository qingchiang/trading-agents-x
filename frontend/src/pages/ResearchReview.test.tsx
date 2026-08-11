import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { api, type ResearchReview } from "../api/client";
import i18n from "../i18n";
import { Router } from "../router";
import ResearchReviewPage from "./ResearchReview";

vi.mock("../api/client", () => ({
  api: { reviews: vi.fn(), recentInstruments: vi.fn() },
}));

const review = {
  outcome_id: 7,
  review_status: "feedback_available",
  lifecycle_actions_allowed: true,
  run_id: "legacy-run",
  ticker: "7203.T",
  instrument_name: "Toyota Motor Corporation",
  instrument_local_name: "トヨタ自動車",
  market: "Asia/Tokyo",
  asset_type: "stock",
  analysis_date: "2026-07-24",
  profile: "standard",
  decision: {
    rating: "Hold", confidence: 0.6, executive_summary: "Summary.", thesis: "**Imported thesis**",
    evidence_refs: [], catalysts: ["Volume growth"], risks: ["Input costs"],
    invalidation_conditions: ["Demand falls"], unresolved_questions: [], time_horizon: "6-12 months",
    scenarios: (["base", "bull", "bear"] as const).map((kind) => ({ kind, core_assumptions: [], outcome: `${kind} outcome`, evidence_refs: [] })),
  },
  outcome: {
    status: "resolved", source_decision_id: 3, source_revision_id: null, benchmark: "^N225",
    market_timezone: "Asia/Tokyo", method_category: "short_term_relative_return",
    method_version: "short_term_relative_return.v1", price_semantics: "exchange_local_daily_close",
    adjustment_semantics: "split_and_dividend_adjusted", horizon_limit: "Full horizon limitation.", limitations: [],
    observation_start: "2026-07-25", observation_end: "2026-08-01", holding_intervals: 5,
    raw_return: 0.03, alpha_return: 0.01, data_available_at: "2026-08-01T20:00:00Z",
    last_checked_at: "2026-08-01T20:00:00Z", next_check_at: null, error_message: null,
  },
  reflection: "Method lesson: Full generated reflection.",
  method_feedback: "Full generated reflection.",
  outcome_reflection: { status: "generated", generated_at: "2026-08-01T20:01:00Z", last_attempted_at: "2026-08-01T20:01:00Z", next_retry_at: null, error_code: null },
  outcome_feedback: { id: 11, status: "eligible", qualification_policy_version: "outcome_feedback_qualification.v1", reasons: [], method_category: "short_term_relative_return", horizon_limit: "Full horizon limitation.", applicability: { schema_version: "1", scope: "instrument", instrument: "7203.T", market: "Asia/Tokyo", research_stages: [], research_domains: [], method_category: "short_term_relative_return", horizon: "short_term" }, qualified_at: "2026-08-01T20:02:00Z", available_at: "2026-08-01T20:02:00Z", retired_at: null },
} as ResearchReview;

beforeEach(async () => {
  vi.resetAllMocks();
  window.history.replaceState(null, "", "/reviews");
  await i18n.changeLanguage("en");
  vi.mocked(api.reviews).mockResolvedValue([review]);
  vi.mocked(api.recentInstruments).mockResolvedValue([]);
});

function renderReviews() {
  return render(<Router><ResearchReviewPage /></Router>);
}

test("renders the source decision, observation, then qualified feedback", async () => {
  renderReviews();
  expect(await screen.findByRole("heading", { name: "Source Research Decision" })).toBeVisible();
  expect(screen.getByRole("heading", { name: "Outcome Observation" })).toBeVisible();
  expect(screen.getByRole("heading", { name: "Method Feedback" })).toBeVisible();
  expect(screen.getByText("Imported thesis")).toBeVisible();
  expect(screen.getByText("Full generated reflection.")).toBeVisible();
  expect(screen.getByText("Five common trading intervals are short-term methodological feedback only.")).toBeVisible();
  expect(screen.queryByText("short_term_relative_return.v1")).toBeNull();
  fireEvent.click(screen.getByText("Method Reflection and audit details"));
  expect(screen.getByText(/short_term_relative_return\.v1/)).toBeVisible();
});

test("uses canonical review filters and deep links", async () => {
  window.history.replaceState(null, "", "/reviews?q=legacy-run#review-legacy-run");
  const { container } = renderReviews();
  await waitFor(() => expect(api.reviews).toHaveBeenCalledWith("?q=legacy-run"));
  await waitFor(() => expect(container.querySelector("#review-legacy-run")).not.toBeNull());
  fireEvent.change(screen.getByLabelText("Review status"), { target: { value: "in_progress" } });
  fireEvent.click(screen.getByRole("button", { name: "Apply" }));
  await waitFor(() =>
    expect(api.reviews).toHaveBeenCalledWith(
      "?q=legacy-run&status_group=in_progress",
    ),
  );
});

test("does not present feedback for lifecycle-inconsistent data", async () => {
  vi.mocked(api.reviews).mockResolvedValue([{ ...review, review_status: "lifecycle_inconsistent", lifecycle_actions_allowed: false, method_feedback: null }]);
  renderReviews();
  expect(await screen.findByText(/inconsistent lifecycle data/)).toBeVisible();
  expect(screen.getByText("No Method Feedback is available yet.")).toBeVisible();
});
