import { FormEvent, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  api,
  type OutcomeFeedbackRetireRequest,
  type ResearchReview,
  type ResearchReviewAuditDetail,
} from "../api/client";
import {
  InstrumentIdentity,
  RecentInstrumentDatalist,
  recentInstrumentListId,
  useRecentInstruments,
} from "../components/Instruments";
import Markdown from "../components/Markdown";
import StatusBadge from "../components/StatusBadge";
import { Link } from "../router";

const statusGroups = [
  "all",
  "needs_attention",
  "in_progress",
  "feedback_available",
  "feedback_ineligible_or_retired",
] as const;

export default function ResearchReview() {
  const { t } = useTranslation();
  const [reviews, setReviews] = useState<ResearchReview[]>([]);
  const initialParams = new URLSearchParams(window.location.search);
  const [q, setQ] = useState(() => initialParams.get("q") ?? "");
  const [ticker, setTicker] = useState(
    () => initialParams.get("ticker") ?? "",
  );
  const [market, setMarket] = useState(
    () => initialParams.get("market") ?? "",
  );
  const [statusGroup, setStatusGroup] = useState(
    () => initialParams.get("status_group") ?? "all",
  );
  const [error, setError] = useState("");
  const [reflectionActionError, setReflectionActionError] = useState("");
  const [reflectionActionErrorOutcomeId, setReflectionActionErrorOutcomeId] = useState<number | null>(null);
  const [actionOutcomeId, setActionOutcomeId] = useState<number | null>(null);
  const [retirement, setRetirement] = useState<{
    feedbackId: number;
    outcomeId: number;
  } | null>(null);
  const [retirementReason, setRetirementReason] = useState<
    OutcomeFeedbackRetireRequest["reason"]
  >("not_useful");
  const [retirementNote, setRetirementNote] = useState("");
  const [retirementError, setRetirementError] = useState("");
  const [actionAnnouncement, setActionAnnouncement] = useState<{
    outcomeId: number;
    message: string;
  } | null>(null);
  const [auditDetails, setAuditDetails] = useState<
    Record<number, ResearchReviewAuditDetail>
  >({});
  const recentInstruments = useRecentInstruments();
  const reviewRefs = useRef<Record<number, HTMLElement | null>>({});
  const auditRequestVersionsRef = useRef<Record<number, number>>({});
  const retirementTriggerRef = useRef<HTMLButtonElement | null>(null);
  const retirementReasonRef = useRef<HTMLSelectElement | null>(null);

  const focusReview = (outcomeId: number) => {
    reviewRefs.current[outcomeId]?.focus();
  };

  const load = async (query = "") => {
    try {
      setReviews(await api.reviews(query));
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("error"));
    }
  };
  useEffect(() => {
    void load(window.location.search);
  }, []);
  useEffect(() => {
    if (!reviews.length || !window.location.hash) return;
    const target = document.getElementById(
      decodeURIComponent(window.location.hash.slice(1)),
    );
    target?.focus();
    target?.scrollIntoView?.({
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "auto"
        : "smooth",
      block: "center",
    });
  }, [reviews]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const params = new URLSearchParams();
    if (q.trim()) params.set("q", q.trim());
    if (ticker.trim()) params.set("ticker", ticker.trim());
    if (market.trim()) params.set("market", market.trim());
    if (statusGroup !== "all") params.set("status_group", statusGroup);
    const query = params.size ? `?${params}` : "";
    window.history.replaceState(null, "", `/reviews${query}`);
    void load(query);
  };
  const loadAuditDetail = async (outcomeId: number, force = false) => {
    if (!force && auditDetails[outcomeId]) return;
    const requestVersion = (auditRequestVersionsRef.current[outcomeId] ?? 0) + 1;
    auditRequestVersionsRef.current[outcomeId] = requestVersion;
    try {
      const detail = await api.reviewAuditDetail(outcomeId);
      if (auditRequestVersionsRef.current[outcomeId] !== requestVersion) return;
      setAuditDetails((current) => ({ ...current, [outcomeId]: detail }));
    } catch (cause) {
      if (auditRequestVersionsRef.current[outcomeId] !== requestVersion) return;
      setError(cause instanceof Error ? cause.message : t("error"));
    }
  };
  const refreshRequestedAuditDetail = (outcomeId: number) => {
    if (auditRequestVersionsRef.current[outcomeId] === undefined) return;
    auditRequestVersionsRef.current[outcomeId] += 1;
    setAuditDetails((current) => {
      const next = { ...current };
      delete next[outcomeId];
      return next;
    });
    void loadAuditDetail(outcomeId, true);
  };
  const regenerateReflection = async (outcomeId: number) => {
    setActionOutcomeId(outcomeId);
    setReflectionActionError("");
    setReflectionActionErrorOutcomeId(null);
    try {
      const accepted = await api.regenerateOutcomeReflection(
        outcomeId,
        crypto.randomUUID(),
      );
      setReviews((current) => current.map((review) =>
        review.outcome_id === outcomeId && review.outcome_reflection
          ? {
              ...review,
              review_status: accepted.review_status,
              outcome_reflection: {
                ...review.outcome_reflection,
                status: accepted.reflection_status ?? review.outcome_reflection.status,
                generation_cycle: accepted.cycle,
              },
            }
          : review
      ));
      refreshRequestedAuditDetail(outcomeId);
      setActionAnnouncement({ outcomeId, message: t("reflectionQueued") });
      focusReview(outcomeId);
    } catch (cause) {
      setReflectionActionError(cause instanceof Error ? cause.message : t("error"));
      setReflectionActionErrorOutcomeId(outcomeId);
      focusReview(outcomeId);
    } finally {
      setActionOutcomeId(null);
    }
  };
  const openRetirement = (
    feedbackId: number,
    outcomeId: number,
    trigger: HTMLButtonElement,
  ) => {
    retirementTriggerRef.current = trigger;
    setRetirement({ feedbackId, outcomeId });
    setRetirementReason("not_useful");
    setRetirementNote("");
    setRetirementError("");
  };
  const closeRetirement = () => {
    setRetirement(null);
    retirementTriggerRef.current?.focus();
  };
  useEffect(() => {
    if (retirement) retirementReasonRef.current?.focus();
  }, [retirement]);
  const retireFeedback = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!retirement) return;
    setActionOutcomeId(retirement.outcomeId);
    setRetirementError("");
    try {
      const retired = await api.retireOutcomeFeedback(retirement.feedbackId, {
        reason: retirementReason,
        note: retirementNote.trim() || null,
      });
      setRetirement(null);
      setReviews((current) => current.map((review) =>
        review.outcome_id === retirement.outcomeId && review.outcome_feedback
          ? {
              ...review,
              review_status: retired.review_status as ResearchReview["review_status"],
              outcome_feedback: {
                ...review.outcome_feedback,
                status: retired.status as NonNullable<
                  ResearchReview["outcome_feedback"]
                >["status"],
                retirement_reason: retired.retirement_reason,
                retirement_note: retired.retirement_note,
                retired_at: retired.retired_at,
              },
            }
          : review
      ));
      refreshRequestedAuditDetail(retirement.outcomeId);
      setActionAnnouncement({
        outcomeId: retirement.outcomeId,
        message: t("retirementSuccess"),
      });
      focusReview(retirement.outcomeId);
    } catch (cause) {
      setRetirementError(cause instanceof Error ? cause.message : t("error"));
    } finally {
      setActionOutcomeId(null);
    }
  };
  return (
    <section>
      <header className="page-header">
        <div>
          <p className="eyebrow">{t("deterministicFeedback")}</p>
          <h1>{t("researchReview")}</h1>
          <p className="subtitle">{t("researchReviewHint")}</p>
        </div>
      </header>
      <form className="panel filter-bar" onSubmit={submit}>
        <label>
          {t("reviewSearch")}
          <input
            id="review-search"
            name="q"
            autoComplete="on"
            value={q}
            onChange={(event) => setQ(event.target.value)}
            placeholder={t("reviewSearchPlaceholder")}
          />
        </label>
        <label>
          {t("ticker")}
          <input
            id="review-ticker"
            name="ticker"
            autoComplete="on"
            list={recentInstrumentListId}
            spellCheck={false}
            value={ticker}
            onChange={(event) => setTicker(event.target.value)}
          />
          <RecentInstrumentDatalist instruments={recentInstruments} />
        </label>
        <label>
          {t("market")}
          <input
            id="review-market"
            name="market"
            autoComplete="on"
            value={market}
            onChange={(event) => setMarket(event.target.value)}
            placeholder="Asia/Tokyo"
          />
        </label>
        <label>
          {t("reviewStatus")}
          <select
            id="review-status-group"
            name="status_group"
            value={statusGroup}
            onChange={(event) => setStatusGroup(event.target.value)}
          >
            {statusGroups.map((group) => (
              <option key={group} value={group}>
                {t(`reviewStatusGroup.${group}`)}
              </option>
            ))}
          </select>
        </label>
        <button className="button primary">{t("apply")}</button>
      </form>
      {error && <div className="alert">{error}</div>}
      <div className="memory-list">
        {reviews.map((review) => (
          <article
            className="panel memory-card"
            id={`review-${review.run_id}`}
            key={review.run_id}
            ref={(element) => {
              reviewRefs.current[review.outcome_id] = element;
            }}
            tabIndex={-1}
          >
            <div className="memory-meta">
              <div>
                <Link
                  className="memory-title-link"
                  to={runDecisionPath(review.run_id)}
                  aria-label={`${t("openResearchDecision")} ${review.ticker}`}
                >
                  <InstrumentIdentity
                    ticker={review.ticker}
                    instrumentName={review.instrument_name}
                    instrumentLocalName={review.instrument_local_name}
                  />
                </Link>
                <div className="memory-run-context">
                  <span>{review.analysis_date}</span>
                  <span>{review.market || "—"}</span>
                  <span title={t(profileDescriptionKey(review.profile))}>
                    {t(review.profile)}
                  </span>
                </div>
              </div>
              <StatusBadge status={review.review_status} />
            </div>
            {actionAnnouncement?.outcomeId === review.outcome_id && (
              <p className="review-action-announcement" role="status">
                {actionAnnouncement.message}
              </p>
            )}
            {review.review_status === "lifecycle_inconsistent" && (
              <div className="alert" role="alert">
                {t("reviewLifecycleInconsistent")}
              </div>
            )}
            <section className="memory-decision">
              <h2>{t("sourceResearchDecision")}</h2>
              <strong>{review.decision.rating}</strong>
              <span>{Math.round(review.decision.confidence * 100)}%</span>
              <Markdown>{review.decision.thesis}</Markdown>
              <details className="memory-decision-details">
                <summary>{t("decisionDetails")}</summary>
                <DecisionDetails review={review} />
              </details>
            </section>
            <section>
              <h2>{t("outcomeObservation")}</h2>
              {review.outcome.status === "resolved" ? (
                <>
                  <div className="returns">
                    <span>
                      {t("rawReturn")} <strong>{percent(review.outcome.raw_return)}</strong>
                    </span>
                    <span>
                      {t("alphaReturn")} <strong>{percent(review.outcome.alpha_return)}</strong>
                    </span>
                    <span>{t("benchmark")} {review.outcome.benchmark}</span>
                    <span>
                      {review.outcome.observation_start} → {review.outcome.observation_end}
                    </span>
                  </div>
                  <p className="memory-lifecycle-note">
                    {t("shortTermReviewWarning")}
                  </p>
                </>
              ) : (
                <p>{t("observationAwaiting")}</p>
              )}
            </section>
            <section>
              <h2>{t("methodFeedback")}</h2>
              {review.review_status === "feedback_available" &&
              review.method_feedback ? (
                <>
                  <Markdown>{review.method_feedback}</Markdown>
                  {review.lifecycle_actions_allowed && review.outcome_feedback && (
                    <div className="feedback-retirement-action">
                      <button
                        className="button compact-button danger"
                        disabled={actionOutcomeId === review.outcome_id}
                        onClick={(event) =>
                          openRetirement(
                            review.outcome_feedback!.id,
                            review.outcome_id,
                            event.currentTarget,
                          )
                        }
                        type="button"
                      >
                        {t("retireFeedback")}
                      </button>
                    </div>
                  )}
                </>
              ) : review.review_status === "feedback_ineligible" ? (
                <>
                  <p>{t("feedbackIneligible")}</p>
                  <p className="memory-lifecycle-note">{t("feedbackIneligibleReason")}</p>
                </>
              ) : review.review_status === "feedback_retired" ? (
                <>
                  <p>{t("feedbackRetired")}</p>
                  {review.outcome_feedback?.retirement_reason && (
                    <p className="memory-lifecycle-note">
                      {t("retirementReason")}: {t(
                        `retirementReasonOptions.${review.outcome_feedback.retirement_reason}`,
                      )}
                    </p>
                  )}
                  {review.outcome_feedback?.retirement_note && (
                    <p className="memory-lifecycle-note">
                      {t("retirementNote")}: {review.outcome_feedback.retirement_note}
                    </p>
                  )}
                </>
              ) : (
                <p>{t("feedbackUnavailable")}</p>
              )}
            </section>
            {review.lifecycle_actions_allowed &&
            ["reflection_invalid", "reflection_failed"].includes(
              review.review_status,
            ) ? (
              <section
                aria-labelledby={`reflection-failure-${review.outcome_id}`}
                className="reflection-regeneration-action"
              >
                <h2 id={`reflection-failure-${review.outcome_id}`}>
                  {t("methodReflection")}
                </h2>
                <p>
                  {review.outcome_reflection?.error_code || t("reflectionFailure")}
                </p>
                {reflectionActionError && reflectionActionErrorOutcomeId === review.outcome_id && (
                  <div
                    className="alert"
                    id={`reflection-action-error-${review.outcome_id}`}
                    role="alert"
                  >
                    {reflectionActionError}
                  </div>
                )}
                <button
                  aria-describedby={
                    reflectionActionErrorOutcomeId === review.outcome_id
                      ? `reflection-action-error-${review.outcome_id}`
                      : undefined
                  }
                  className="button compact-button"
                  disabled={actionOutcomeId === review.outcome_id}
                  onClick={() => void regenerateReflection(review.outcome_id)}
                  type="button"
                >
                  {t("regenerateReflection")}
                </button>
              </section>
            ) : review.outcome_reflection?.generation_cycle?.status === "queued" ? (
              <section
                aria-labelledby={`reflection-queued-${review.outcome_id}`}
                className="reflection-regeneration-action"
              >
                <h2 id={`reflection-queued-${review.outcome_id}`}>
                  {t("methodReflection")}
                </h2>
                <p>{t("reflectionQueued")}</p>
                <button className="button compact-button" disabled type="button">
                  {t("statusQueued")}
                </button>
              </section>
            ) : null}
            <details
              className="memory-decision-details"
              onToggle={(event) => {
                if (event.currentTarget.open) {
                  void loadAuditDetail(review.outcome_id);
                }
              }}
            >
              <summary>{t("methodReflectionAndAudit")}</summary>
              {auditDetails[review.outcome_id]?.reflection && (
                <Markdown>{String(auditDetails[review.outcome_id].reflection)}</Markdown>
              )}
              {!auditDetails[review.outcome_id] && <p>{t("loading")}</p>}
              {auditDetails[review.outcome_id] && <AuditLifecycle detail={auditDetails[review.outcome_id]} />}
              <p className="memory-lifecycle-note">
                {review.outcome.method_version} · {review.outcome.market_timezone} ·{" "}
                {review.outcome.price_semantics} / {review.outcome.adjustment_semantics}
              </p>
              <p className="memory-lifecycle-note">{review.outcome.horizon_limit}</p>
              {review.outcome.limitations.map((limitation) => (
                <p className="memory-lifecycle-note" key={limitation}>
                  {limitation}
                </p>
              ))}
              <p className="memory-lifecycle-note">
                {review.outcome.status} · {review.outcome_reflection?.status ?? "—"} ·{" "}
                {review.outcome_feedback?.status ?? "—"}
              </p>
              {review.outcome_feedback && (
                <p className="memory-lifecycle-note">
                  {review.outcome_feedback.qualification_policy_version} ·{" "}
                  {review.outcome_feedback.reasons.join(", ") || "—"}
                </p>
              )}
              {auditDetails[review.outcome_id] && (
                <AuditAttempts detail={auditDetails[review.outcome_id]} />
              )}
            </details>
          </article>
        ))}
        {reviews.length === 0 && (
          <div className="empty-state">{t("noResearchReviews")}</div>
        )}
      </div>
      {retirement && (
        <div className="retirement-dialog-layer">
          <form
            aria-describedby="retirement-confirmation"
            aria-labelledby="retirement-dialog-title"
            aria-modal="true"
            className="retirement-dialog"
            onKeyDown={(event) => {
              if (event.key === "Escape" && actionOutcomeId === null) {
                closeRetirement();
              }
              if (event.key === "Tab") {
                const focusable = Array.from(
                  event.currentTarget.querySelectorAll<HTMLElement>(
                    "button:not([disabled]), select:not([disabled]), textarea:not([disabled])",
                  ),
                );
                const currentIndex = focusable.indexOf(document.activeElement as HTMLElement);
                const nextIndex = event.shiftKey
                  ? (currentIndex <= 0 ? focusable.length - 1 : currentIndex - 1)
                  : (currentIndex === focusable.length - 1 ? 0 : currentIndex + 1);
                if (currentIndex !== -1) {
                  event.preventDefault();
                  focusable[nextIndex]?.focus();
                }
              }
            }}
            onSubmit={(event) => void retireFeedback(event)}
            role="dialog"
          >
            <h2 id="retirement-dialog-title">{t("retireMethodFeedback")}</h2>
            <p id="retirement-confirmation">{t("retirementConfirmation")}</p>
            <label>
              {t("retirementReason")}
              <select
                ref={retirementReasonRef}
                onChange={(event) => setRetirementReason(
                  event.target.value as OutcomeFeedbackRetireRequest["reason"],
                )}
                value={retirementReason}
              >
                {(["not_useful", "too_specific", "misleading", "other"] as const).map((reason) => (
                  <option key={reason} value={reason}>
                    {t(`retirementReasonOptions.${reason}`)}
                  </option>
                ))}
              </select>
            </label>
            <label>
              {t("retirementNote")}
              <textarea
                maxLength={1000}
                onChange={(event) => setRetirementNote(event.target.value)}
                value={retirementNote}
              />
            </label>
            {retirementError && <div className="alert" id="retirement-action-error" role="alert">{retirementError}</div>}
            <div className="retirement-dialog-actions">
              <button
                className="button"
                disabled={actionOutcomeId !== null}
                onClick={closeRetirement}
                type="button"
              >
                {t("cancel")}
              </button>
              <button
                className="button danger"
                aria-describedby={retirementError ? "retirement-action-error" : undefined}
                disabled={actionOutcomeId !== null}
                type="submit"
              >
                {t("retireFeedback")}
              </button>
            </div>
          </form>
        </div>
      )}
    </section>
  );
}

function AuditAttempts({ detail }: { detail: ResearchReviewAuditDetail }) {
  const { t } = useTranslation();
  return (
    <div className="memory-lifecycle-note">
      <p>
        {t("reviewAttemptCount", { count: detail.aggregate_usage.attempt_count })} · {detail.aggregate_usage.usage_status}
      </p>
      <pre>{JSON.stringify(detail.aggregate_usage, null, 2)}</pre>
      {detail.attempts.map((attempt) => (
        <details key={attempt.id}>
          <summary>
            #{attempt.sequence} · {attempt.attempt_kind} · {attempt.outcome ?? t("statusRunning")}
          </summary>
          <p>{attempt.attempt_schema_version} · {attempt.candidate_schema_version ?? "—"}</p>
          <p>
            {attempt.started_at} · {attempt.finished_at ?? t("statusRunning")}
          </p>
          <pre>{JSON.stringify(attempt.usage, null, 2)}</pre>
          <p>{JSON.stringify(attempt.diagnostics ?? {})}</p>
          <p>{attempt.validation_issues?.join(", ") ?? "—"}</p>
          {attempt.invalid_candidate && <pre>{attempt.invalid_candidate}</pre>}
        </details>
      ))}
    </div>
  );
}

function AuditLifecycle({ detail }: { detail: ResearchReviewAuditDetail }) {
  const { t } = useTranslation();
  const { outcome, outcome_feedback: feedback, outcome_reflection: reflection } = detail.review;
  return <>
    <p className="memory-lifecycle-note">{t("outcomeObservation")}: {outcome.resolved_at ?? "—"} · {outcome.data_available_at ?? "—"} · {outcome.last_checked_at ?? "—"}</p>
    {reflection && <p className="memory-lifecycle-note">{t("methodReflection")}: {reflection.created_at} · {reflection.generated_at ?? "—"} · {reflection.last_attempted_at ?? "—"}</p>}
    {feedback && <p className="memory-lifecycle-note">{t("methodFeedback")}: {feedback.qualified_at} · {feedback.available_at} · {feedback.retired_at ?? "—"}</p>}
  </>;
}

function DecisionDetails({ review }: { review: ResearchReview }) {
  const { t } = useTranslation();
  return (
    <div className="memory-decision-details-body">
      <div className="memory-decision-grid">
        <div className="memory-decision-field">
          <strong>{t("horizon")}</strong>
          <Markdown>{review.decision.time_horizon}</Markdown>
        </div>
        <div className="memory-decision-field">
          <strong>{t("scenarios")}</strong>
          {review.decision.scenarios.map((scenario) => (
            <div key={scenario.kind}>
              <strong>{t(`${scenario.kind}Scenario`)}</strong>
              <Markdown>{scenario.outcome}</Markdown>
              {scenario.core_assumptions.length > 0 && (
                <ul>
                  {scenario.core_assumptions.map((assumption) => (
                    <li key={assumption}><Markdown>{assumption}</Markdown></li>
                  ))}
                </ul>
              )}
            </div>
          ))}
        </div>
        <DecisionList
          title={t("catalysts")}
          items={review.decision.catalysts ?? []}
        />
        <DecisionList title={t("risks")} items={review.decision.risks ?? []} />
        <DecisionList
          title={t("invalidation")}
          items={review.decision.invalidation_conditions ?? []}
        />
        <DecisionList
          title={t("unresolvedQuestions")}
          items={review.decision.unresolved_questions ?? []}
        />
      </div>
    </div>
  );
}

function DecisionList({ title, items }: { title: string; items: string[] }) {
  return (
    <div className="memory-decision-field">
      <strong>{title}</strong>
      {items.length ? (
        <ul>
          {items.map((item) => (
            <li key={item}>
              <Markdown>{item}</Markdown>
            </li>
          ))}
        </ul>
      ) : (
        <span>—</span>
      )}
    </div>
  );
}

function percent(value: number | null) {
  return value == null ? "—" : `${value >= 0 ? "+" : ""}${(value * 100).toFixed(1)}%`;
}

function runDecisionPath(runId: string): string {
  return `/runs/${encodeURIComponent(runId)}?view=decision`;
}

function profileDescriptionKey(profile: ResearchReview["profile"]): string {
  return {
    fast: "profileFastDesc",
    standard: "profileStandardDesc",
    deep: "profileDeepDesc",
  }[profile];
}
