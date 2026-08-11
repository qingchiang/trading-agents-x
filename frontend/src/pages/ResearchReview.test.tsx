import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";

import { api, type ResearchReview, type ResearchReviewAuditDetail } from "../api/client";
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
    review_status: "awaiting_reflection",
    reflection_status: "pending",
  });
  vi.mocked(api.retireOutcomeFeedback).mockResolvedValue({
    status: "retired",
    review_status: "feedback_retired",
    retirement_reason: "not_useful",
    retirement_note: null,
    retired_at: "2026-08-12T00:00:00Z",
  });
  vi.mocked(api.recentInstruments).mockResolvedValue([]);
});

function renderReviews() {
  return render(<Router><ResearchReviewPage /></Router>);
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
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
  fireEvent.click(screen.getByText("Decision details"));
  expect(screen.getByText("6-12 months")).toBeVisible();
  expect(screen.getByText("base outcome")).toBeVisible();
  expect(screen.getByText("bull outcome")).toBeVisible();
  expect(screen.getByText("bear outcome")).toBeVisible();
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
  window.history.replaceState(null, "", "/reviews?status_group=needs_attention");
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
  vi.mocked(api.reviews).mockResolvedValueOnce([failed]);
  renderReviews();
  fireEvent.click(await screen.findByRole("button", { name: "Regenerate Method Reflection" }));
  await waitFor(() => expect(api.regenerateOutcomeReflection).toHaveBeenCalledWith(7, expect.any(String)));
  expect(await screen.findByRole("status")).toHaveTextContent("Reflection regeneration is queued.");
  expect(screen.getByRole("button", { name: "Queued" })).toBeDisabled();
  expect(screen.getByRole("article")).toHaveFocus();
  expect(api.reviews).toHaveBeenCalledTimes(1);
});

test("ignores an audit response started before regeneration", async () => {
  const failed = {
    ...review,
    review_status: "reflection_failed" as const,
    method_feedback: null,
    outcome_reflection: {
      ...review.outcome_reflection!,
      status: "retryable_failure" as const,
      error_code: "TransportError",
      generation_cycle: null,
    },
    outcome_feedback: null,
  };
  const stale = deferred<ResearchReviewAuditDetail>();
  const fresh = deferred<ResearchReviewAuditDetail>();
  const aggregateUsage = {
    usage_status: "not_reported" as const,
    attempt_count: 1,
    llm_calls: 1,
    input_tokens: null,
    output_tokens: null,
    cache_hit_input_tokens: null,
    cache_miss_input_tokens: null,
    reasoning_output_tokens: null,
    wall_time_seconds: null,
    provider_reported_cost_usd: null,
  };
  vi.mocked(api.reviews).mockResolvedValueOnce([failed]);
  vi.mocked(api.reviewAuditDetail)
    .mockReset()
    .mockReturnValueOnce(stale.promise)
    .mockReturnValueOnce(fresh.promise);
  renderReviews();

  fireEvent.click(await screen.findByText("Method Reflection and audit details"));
  await waitFor(() => expect(api.reviewAuditDetail).toHaveBeenCalledTimes(1));
  fireEvent.click(screen.getByRole("button", { name: "Regenerate Method Reflection" }));
  await waitFor(() => expect(api.reviewAuditDetail).toHaveBeenCalledTimes(2));

  fresh.resolve({
    review: failed,
    reflection: "Fresh audit detail.",
    attempts: [],
    aggregate_usage: aggregateUsage,
  });
  expect(await screen.findByText("Fresh audit detail.")).toBeVisible();
  stale.resolve({
    review: failed,
    reflection: "Stale audit detail.",
    attempts: [],
    aggregate_usage: aggregateUsage,
  });
  await waitFor(() => expect(screen.queryByText("Stale audit detail.")).toBeNull());
});

test("confirms a typed, auditable Feedback retirement outside its prose", async () => {
  window.history.replaceState(null, "", "/reviews?status_group=feedback_available");
  vi.mocked(api.reviews).mockResolvedValueOnce([review]);
  const retiredReview = {
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
  vi.mocked(api.retireOutcomeFeedback).mockResolvedValueOnce({
    status: "retired",
    review_status: "feedback_retired",
    retirement_reason: "misleading",
    retirement_note: "It overstates a one-off result.",
    retired_at: "2026-08-12T00:00:00Z",
  });
  vi.mocked(api.reviewAuditDetail)
    .mockResolvedValueOnce({
      review,
      reflection: "Method lesson: Full generated reflection.",
      attempts: [],
      aggregate_usage: {
        usage_status: "not_reported", attempt_count: 1, llm_calls: 1,
        input_tokens: null, output_tokens: null, cache_hit_input_tokens: null,
        cache_miss_input_tokens: null, reasoning_output_tokens: null,
        wall_time_seconds: null, provider_reported_cost_usd: null,
      },
    })
    .mockResolvedValueOnce({
      review: retiredReview,
      reflection: "Method lesson: Full generated reflection.",
      attempts: [],
      aggregate_usage: {
        usage_status: "not_reported", attempt_count: 1, llm_calls: 1,
        input_tokens: null, output_tokens: null, cache_hit_input_tokens: null,
        cache_miss_input_tokens: null, reasoning_output_tokens: null,
        wall_time_seconds: null, provider_reported_cost_usd: null,
      },
  });
  renderReviews();

  const auditSummary = await screen.findByText("Method Reflection and audit details");
  const auditDisclosure = auditSummary.closest("details");
  expect(auditDisclosure).not.toBeNull();
  fireEvent.click(auditSummary);
  await screen.findByText("Method lesson: Full generated reflection.");

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
  await waitFor(() => expect(api.reviewAuditDetail).toHaveBeenCalledTimes(2));
  expect(
    await within(auditDisclosure!).findByText(/2026-08-12T00:00:00Z/),
  ).toBeVisible();
  expect(screen.getByText("Method Feedback has been retired.")).toBeVisible();
  expect(screen.getByText(/Reason: Misleading/)).toBeVisible();
  expect(screen.getByText(/Optional note: It overstates a one-off result\./)).toBeVisible();
  expect(api.reviews).toHaveBeenCalledTimes(1);
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

test("keeps invalid candidates closed, escaped, and inside audit details", async () => {
  const invalidCandidate = '<img src=x onerror="alert(1)"> prompt-like text';
  vi.mocked(api.reviews).mockResolvedValue([
    {
      ...review,
      review_status: "reflection_invalid",
      method_feedback: null,
      outcome_reflection: { ...review.outcome_reflection!, status: "invalid" },
      outcome_feedback: null,
    },
  ]);
  vi.mocked(api.reviewAuditDetail).mockResolvedValue({
    review: {
      ...review,
      review_status: "reflection_invalid",
      method_feedback: null,
      outcome_reflection: { ...review.outcome_reflection!, status: "invalid" },
      outcome_feedback: null,
    },
    reflection: null,
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
    attempts: [{
      id: 1,
      sequence: 1,
      attempt_kind: "initial",
      generation_cycle_id: "cycle-1",
      origin: "automatic",
      trigger: "initial_generation",
      outcome: "invalid",
      attempt_schema_version: "outcome_reflection_attempt.v1",
      candidate_schema_version: "outcome_reflection.v1",
      started_at: "2026-08-01T20:00:00Z",
      finished_at: "2026-08-01T20:01:00Z",
      diagnostics: { code: "schema_invalid" },
      validation_issues: ["method_lesson is required"],
      invalid_candidate: invalidCandidate,
      invalid_candidate_digest: "abc123",
      invalid_candidate_length: invalidCandidate.length,
      usage: {
        usage_status: "not_reported",
        llm_calls: 1,
        input_tokens: null,
        output_tokens: null,
        cache_hit_input_tokens: null,
        cache_miss_input_tokens: null,
        reasoning_output_tokens: null,
        wall_time_seconds: null,
        provider_reported_cost_usd: null,
      },
    }],
  });

  const { container } = renderReviews();
  await screen.findByRole("heading", { name: "Method Feedback" });
  expect(screen.queryByText(invalidCandidate)).toBeNull();

  fireEvent.click(screen.getByText("Method Reflection and audit details"));
  await waitFor(() => expect(api.reviewAuditDetail).toHaveBeenCalledWith(7));
  fireEvent.click(screen.getByText(/#1 · initial · invalid/));

  expect(await screen.findByText(invalidCandidate)).toBeVisible();
  expect(screen.getAllByText(/"llm_calls": 1/)).toHaveLength(2);
  expect(screen.getByText(/Observation:/)).toBeVisible();
  expect(screen.getByText(/Reflection:/)).toBeVisible();
  expect(container.querySelector("img")).toBeNull();
});

test("localizes Review audit labels in every supported language", async () => {
  for (const language of ["en", "zh-CN", "ja"] as const) {
    await i18n.changeLanguage(language);
    for (const key of [
      "reviewAttemptCount",
      "outcomeObservation",
      "methodReflection",
      "methodFeedback",
    ]) {
      expect(i18n.t(key, { count: 2 })).not.toContain(key);
    }
  }
  await i18n.changeLanguage("en");
});

test("associates regeneration errors with the action and keeps focus in its Review", async () => {
  const failed = {
    ...review,
    review_status: "reflection_failed" as const,
    method_feedback: null,
    outcome_reflection: {
      ...review.outcome_reflection!,
      status: "retryable_failure" as const,
      error_code: "TransportError",
      generation_cycle: null,
    },
    outcome_feedback: null,
  };
  vi.mocked(api.reviews).mockResolvedValue([failed]);
  vi.mocked(api.regenerateOutcomeReflection).mockRejectedValueOnce(new Error("network unavailable"));
  const { container } = renderReviews();

  const action = await screen.findByRole("button", { name: "Regenerate Method Reflection" });
  fireEvent.click(action);

  const error = await screen.findByRole("alert");
  expect(error).toHaveTextContent("network unavailable");
  expect(action).toHaveAttribute("aria-describedby", error.id);
  expect(container.querySelector("#review-legacy-run")).toHaveFocus();
});

test("traps and restores focus for Feedback retirement confirmation", async () => {
  renderReviews();
  const trigger = await screen.findByRole("button", { name: "Retire Feedback" });
  trigger.focus();
  fireEvent.click(trigger);

  const dialog = screen.getByRole("dialog", { name: "Retire Method Feedback" });
  const reason = within(dialog).getByLabelText("Reason");
  await waitFor(() => expect(reason).toHaveFocus());
  fireEvent.keyDown(reason, { key: "Tab", shiftKey: true });
  expect(within(dialog).getByRole("button", { name: "Retire Feedback" })).toHaveFocus();
  fireEvent.keyDown(dialog, { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(trigger).toHaveFocus();
});
