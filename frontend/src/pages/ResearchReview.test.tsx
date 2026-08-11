import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { api, type ResearchReview } from "../api/client";
import i18n from "../i18n";
import { Router } from "../router";
import ResearchReviewPage from "./ResearchReview";

vi.mock("../api/client", () => ({
  api: {
    reviews: vi.fn(), reviewAuditDetail: vi.fn(), recentInstruments: vi.fn(),
    regenerateOutcomeReflection: vi.fn(),
    retireOutcomeFeedback: vi.fn(),
  },
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
    raw_return: 0.03, alpha_return: 0.01, resolved_at: "2026-08-01T20:00:00Z", data_available_at: "2026-08-01T20:00:00Z",
    last_checked_at: "2026-08-01T20:00:00Z", next_check_at: null, error_message: null,
  },
  reflection: "Method lesson: Full generated reflection.",
  method_feedback: "Full generated reflection.",
  outcome_reflection: { status: "generated", created_at: "2026-08-01T20:00:00Z", generated_at: "2026-08-01T20:01:00Z", last_attempted_at: "2026-08-01T20:01:00Z", next_retry_at: null, error_code: null },
  outcome_feedback: { id: 11, status: "eligible", qualification_policy_version: "outcome_feedback_qualification.v1", reasons: [], method_category: "short_term_relative_return", horizon_limit: "Full horizon limitation.", applicability: { schema_version: "1", scope: "instrument", instrument: "7203.T", market: "Asia/Tokyo", research_stages: [], research_domains: [], method_category: "short_term_relative_return", horizon: "short_term" }, qualified_at: "2026-08-01T20:02:00Z", available_at: "2026-08-01T20:02:00Z", retirement_reason: null, retirement_note: null, retired_at: null },
} as ResearchReview;

beforeEach(async () => {
  vi.resetAllMocks();
  window.history.replaceState(null, "", "/reviews");
  await i18n.changeLanguage("en");
  vi.mocked(api.reviews).mockResolvedValue([review]);
  vi.mocked(api.reviewAuditDetail).mockResolvedValue({
    review,
    reflection: "Method lesson: Full generated reflection.",
    attempts: [],
    aggregate_usage: {
      usage_status: "not_reported",
      attempt_count: 1,
      llm_calls: 1,
      input_tokens: null,
      output_tokens: null,
      cache_hit_input_tokens: null,
      cache_miss_input_tokens: null,
      reasoning_output_tokens: null,
      wall_time_seconds: null,
      provider_reported_cost_usd: null,
    },
  });
  vi.mocked(api.regenerateOutcomeReflection).mockResolvedValue({
    cycle: {
      id: "cycle-1", outcome_id: 7, status: "queued", origin: "manual",
      trigger: "user_regeneration", retry_ordinal: 0,
      queued_at: "2026-08-05T00:00:00Z", due_at: "2026-08-05T00:00:00Z",
    },
  });
  vi.mocked(api.retireOutcomeFeedback).mockResolvedValue({
    status: "retired",
    retirement_reason: "not_useful",
    retirement_note: null,
    retired_at: "2026-08-12T00:00:00Z",
  });
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
  await waitFor(() => expect(api.reviewAuditDetail).toHaveBeenCalledWith(7));
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

test("queues one regeneration in the Reflection failure section", async () => {
  const baseReflection = review.outcome_reflection!;
  const failed = {
    ...review,
    review_status: "reflection_failed" as const,
    method_feedback: null,
    outcome_reflection: {
      ...baseReflection,
      status: "retryable_failure" as const,
      error_code: "TransportError",
      generation_cycle: null,
    },
    outcome_feedback: null,
  };
  const queued = {
    ...failed,
    review_status: "awaiting_reflection" as const,
    outcome_reflection: {
      ...failed.outcome_reflection,
      status: "pending" as const,
      generation_cycle: {
        id: "cycle-1", outcome_id: 7, status: "queued" as const, origin: "manual" as const,
        trigger: "user_regeneration", retry_ordinal: 0,
        queued_at: "2026-08-05T00:00:00Z", due_at: "2026-08-05T00:00:00Z",
      },
    },
  };
  vi.mocked(api.reviews).mockResolvedValueOnce([failed]).mockResolvedValueOnce([queued]);
  renderReviews();
  fireEvent.click(await screen.findByRole("button", { name: "Regenerate Method Reflection" }));
  await waitFor(() => expect(api.regenerateOutcomeReflection).toHaveBeenCalledWith(7, expect.any(String)));
  expect(await screen.findByText("Reflection regeneration is queued.")).toBeVisible();
});

test("confirms a typed, auditable Feedback retirement outside its prose", async () => {
  const retired = {
    ...review,
    review_status: "feedback_retired" as const,
    method_feedback: null,
    outcome_feedback: {
      ...review.outcome_feedback!,
      status: "retired" as const,
      retirement_reason: "misleading" as const,
      retirement_note: "It overstates a one-off result.",
      retired_at: "2026-08-12T00:00:00Z",
    },
  };
  vi.mocked(api.reviews).mockResolvedValueOnce([review]).mockResolvedValueOnce([retired]);
  renderReviews();

  fireEvent.click(await screen.findByRole("button", { name: "Retire Feedback" }));
  const dialog = screen.getByRole("dialog", { name: "Retire Method Feedback" });
  expect(within(dialog).getByText(/irreversible/)).toBeVisible();
  expect(within(dialog).getByText(/does not disable future settlement/)).toBeVisible();
  fireEvent.change(within(dialog).getByLabelText("Reason"), {
    target: { value: "misleading" },
  });
  fireEvent.change(within(dialog).getByLabelText("Optional note"), {
    target: { value: "It overstates a one-off result." },
  });
  fireEvent.click(within(dialog).getByRole("button", { name: "Retire Feedback" }));

  await waitFor(() => expect(api.retireOutcomeFeedback).toHaveBeenCalledWith(11, {
    reason: "misleading",
    note: "It overstates a one-off result.",
  }));
  expect(await screen.findByRole("status")).toHaveTextContent("Method Feedback retired.");
  expect(screen.getByText("Method Feedback has been retired.")).toBeVisible();
});

test("allows cancellation and keeps retirement errors with the confirmation", async () => {
  vi.mocked(api.retireOutcomeFeedback).mockRejectedValueOnce(new Error("not eligible"));
  renderReviews();

  fireEvent.click(await screen.findByRole("button", { name: "Retire Feedback" }));
  fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(api.retireOutcomeFeedback).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "Retire Feedback" }));
  const dialog = screen.getByRole("dialog", { name: "Retire Method Feedback" });
  fireEvent.click(within(dialog).getByRole("button", { name: "Retire Feedback" }));
  expect(await within(dialog).findByRole("alert")).toHaveTextContent("not eligible");
  expect(screen.getByRole("dialog", { name: "Retire Method Feedback" })).toBeVisible();
});

test("localizes Feedback retirement reasons and confirmation in every supported language", async () => {
  for (const [language, reason] of [
    ["en", "Misleading"],
    ["zh-CN", "具有误导性"],
    ["ja", "誤解を招く"],
  ] as const) {
    await i18n.changeLanguage(language);
    expect(i18n.t("retirementReasonOptions.misleading")).toBe(reason);
    expect(i18n.t("retirementConfirmation")).not.toContain("retirementConfirmation");
  }
  await i18n.changeLanguage("en");
});
