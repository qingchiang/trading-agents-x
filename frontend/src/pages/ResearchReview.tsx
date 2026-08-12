import { FormEvent, type ReactNode, useEffect, useRef, useState } from "react";
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
  const auditLoadingRef = useRef<Record<number, boolean>>({});
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
    if (!force && auditLoadingRef.current[outcomeId]) return;
    const requestVersion = (auditRequestVersionsRef.current[outcomeId] ?? 0) + 1;
    auditRequestVersionsRef.current[outcomeId] = requestVersion;
    auditLoadingRef.current[outcomeId] = true;
    try {
      const detail = await api.reviewAuditDetail(outcomeId);
      if (auditRequestVersionsRef.current[outcomeId] !== requestVersion) return;
      setAuditDetails((current) => ({ ...current, [outcomeId]: detail }));
    } catch (cause) {
      if (auditRequestVersionsRef.current[outcomeId] !== requestVersion) return;
      setError(cause instanceof Error ? cause.message : t("error"));
    } finally {
      if (auditRequestVersionsRef.current[outcomeId] === requestVersion) {
        auditLoadingRef.current[outcomeId] = false;
      }
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
      <p
        aria-live="polite"
        className="review-action-announcement"
        role="status"
      >
        {actionAnnouncement?.message ?? ""}
      </p>
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
                  <span
                    aria-label={`${t("profile")}: ${t(review.profile)}`}
                    className="memory-profile"
                    title={t(profileDescriptionKey(review.profile))}
                  >
                    {t(review.profile)}
                  </span>
                </div>
              </div>
              <StatusBadge status={review.review_status} />
            </div>
            {review.review_status === "lifecycle_inconsistent" && (
              <div className="alert" role="alert">
                {t("reviewLifecycleInconsistent")}
              </div>
            )}
            <section className="review-section">
              <h2 className="review-section-title">{t("sourceResearchDecision")}</h2>
              <div className="memory-decision">
                <div className="review-decision-meta">
                  <strong className="review-rating">{review.decision.rating}</strong>
                  <span className="review-confidence">
                    {t("confidence")} {Math.round(review.decision.confidence * 100)}%
                  </span>
                </div>
                <div className="review-decision-copy">
                  <Markdown>{review.decision.thesis}</Markdown>
                  <div className="review-decision-horizon">
                    <strong>{t("horizon")}</strong>
                    <Markdown>{review.decision.time_horizon}</Markdown>
                  </div>
                </div>
              </div>
              <details className="memory-decision-details">
                <summary>{t("decisionDetails")}</summary>
                <DecisionDetails review={review} />
              </details>
            </section>
            <section className="review-section">
              <h2 className="review-section-title">{t("outcomeObservation")}</h2>
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
            <section className="review-section">
              <h2 className="review-section-title">{t("methodFeedback")}</h2>
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
                <p>{t(feedbackStateMessageKey(review.review_status))}</p>
              )}
            </section>
            {review.lifecycle_actions_allowed &&
            ["reflection_invalid", "reflection_failed"].includes(
              review.review_status,
            ) ? (
              <section
                aria-labelledby={`reflection-failure-${review.outcome_id}`}
                className="review-section reflection-regeneration-action"
              >
                <h2 className="review-section-title" id={`reflection-failure-${review.outcome_id}`}>
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
                className="review-section reflection-regeneration-action"
              >
                <h2 className="review-section-title" id={`reflection-queued-${review.outcome_id}`}>
                  {t("methodReflection")}
                </h2>
                <p>{t("reflectionQueued")}</p>
                <button className="button compact-button" disabled type="button">
                  {t("statusQueued")}
                </button>
              </section>
            ) : null}
            <ReviewDetailDisclosure
              className="review-reflection-details"
              detail={auditDetails[review.outcome_id]}
              loadingLabel={t("loading")}
              onOpen={() => void loadAuditDetail(review.outcome_id)}
              summary={t("fullReflectionAnalysis")}
            >
              {(detail) => detail.reflection ? (
                <ReflectionAnalysis reflection={String(detail.reflection)} />
              ) : (
                <p>{t("reflectionUnavailable")}</p>
              )}
            </ReviewDetailDisclosure>
            <ReviewDetailDisclosure
              className="review-audit-details"
              detail={auditDetails[review.outcome_id]}
              loadingLabel={t("loading")}
              onOpen={() => void loadAuditDetail(review.outcome_id)}
              summary={t("generationAndAuditDetails")}
            >
              {(detail) => <AuditDetail detail={detail} />}
            </ReviewDetailDisclosure>
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

function ReflectionAnalysis({ reflection }: { reflection: string }) {
  const { t } = useTranslation();
  const match = reflection.trim().match(
    /^Directional assessment:\s*([^\n]+)\nSource-decision evidence lesson:\s*([\s\S]*?)\nMethod lesson(?::\s*|\s*\n)([\s\S]+)$/,
  );

  if (!match) return <Markdown>{reflection}</Markdown>;

  const assessment = match[1].trim().toLowerCase();
  const isKnownAssessment = ["consistent", "mixed", "inconsistent"].includes(assessment);
  const assessmentKey = isKnownAssessment
    ? `directionalAssessmentValues.${assessment}`
    : null;

  return (
    <dl className="reflection-analysis">
      <div>
        <dt>{t("directionalAssessment")}</dt>
        <dd className={`reflection-assessment${isKnownAssessment ? ` reflection-assessment-${assessment}` : ""}`}>
          {assessmentKey ? t(assessmentKey) : match[1].trim()}
        </dd>
      </div>
      <div>
        <dt>{t("sourceDecisionEvidenceLesson")}</dt>
        <dd><Markdown>{match[2].trim()}</Markdown></dd>
      </div>
      <div>
        <dt>{t("methodLesson")}</dt>
        <dd><Markdown>{match[3].trim()}</Markdown></dd>
      </div>
    </dl>
  );
}

function ReviewDetailDisclosure({
  children,
  className,
  detail,
  loadingLabel,
  onOpen,
  summary,
}: {
  children: (detail: ResearchReviewAuditDetail) => ReactNode;
  className: string;
  detail: ResearchReviewAuditDetail | undefined;
  loadingLabel: string;
  onOpen: () => void;
  summary: string;
}) {
  return (
    <details
      className={`memory-decision-details ${className}`}
      onToggle={(event) => {
        if (event.currentTarget.open) onOpen();
      }}
    >
      <summary>{summary}</summary>
      <div className="review-disclosure-body">
        {detail ? children(detail) : <p>{loadingLabel}</p>}
      </div>
    </details>
  );
}

function AuditAttempts({ detail }: { detail: ResearchReviewAuditDetail }) {
  const { t, i18n } = useTranslation();
  return (
    <section className="review-audit-section">
      <h3>{t("reflectionAttempts")}</h3>
      {detail.attempts.length === 0 && <p>{t("noReflectionAttempts")}</p>}
      {detail.attempts.map((attempt) => (
        <details className="review-attempt" key={attempt.id}>
          <summary>
            #{attempt.sequence} · {attempt.attempt_kind} · {attempt.outcome ?? t("statusRunning")}
          </summary>
          <div className="review-attempt-body">
            <KeyValueTable rows={[
              [t("attemptSchema"), attempt.attempt_schema_version],
              [t("candidateSchema"), attempt.candidate_schema_version],
              [t("origin"), attempt.origin],
              [t("trigger"), attempt.trigger],
              [t("startedAt"), <AuditTime locale={i18n.language} value={attempt.started_at} />],
              [t("finishedAt"), <AuditTime locale={i18n.language} value={attempt.finished_at} />],
            ]} />
            <UsageTable usage={attempt.usage} />
            {attempt.diagnostics && Object.keys(attempt.diagnostics).length > 0 && (
              <div className="review-audit-subsection">
                <h4>{t("diagnostics")}</h4>
                <KeyValueTable rows={Object.entries(attempt.diagnostics)} />
              </div>
            )}
            {attempt.validation_issues && attempt.validation_issues.length > 0 && (
              <div className="review-audit-subsection">
                <h4>{t("validationIssues")}</h4>
                <ul>{attempt.validation_issues.map((issue) => <li key={issue}>{issue}</li>)}</ul>
              </div>
            )}
            {attempt.invalid_candidate && (
              <details className="review-invalid-candidate">
                <summary>{t("invalidGeneratedCandidate")}</summary>
                <p>
                  {t("digest")}: {attempt.invalid_candidate_digest ?? "—"} ·{" "}
                  {t("length")}: {formatAuditNumber(i18n.language, attempt.invalid_candidate_length)}
                </p>
                <pre>{attempt.invalid_candidate}</pre>
              </details>
            )}
          </div>
        </details>
      ))}
    </section>
  );
}

function AuditLifecycle({ detail }: { detail: ResearchReviewAuditDetail }) {
  const { t, i18n } = useTranslation();
  const { outcome, outcome_feedback: feedback, outcome_reflection: reflection } = detail.review;
  return (
    <section className="review-audit-section">
      <h3>{t("lifecycle")}</h3>
      <div className="review-table-scroll">
        <table className="review-audit-table">
          <thead><tr>
            <th>{t("stage")}</th><th>{t("status")}</th><th>{t("createdOrResolved")}</th>
            <th>{t("availableOrCompleted")}</th><th>{t("lastUpdated")}</th>
          </tr></thead>
          <tbody>
            <tr>
              <th scope="row">{t("outcomeObservation")}</th><td>{outcome.status}</td>
              <td><AuditTime locale={i18n.language} value={outcome.resolved_at} /></td>
              <td><AuditTime locale={i18n.language} value={outcome.data_available_at} /></td>
              <td><AuditTime locale={i18n.language} value={outcome.last_checked_at} /></td>
            </tr>
            <tr>
              <th scope="row">{t("methodReflection")}</th><td>{reflection?.status ?? "—"}</td>
              <td><AuditTime locale={i18n.language} value={reflection?.created_at} /></td>
              <td><AuditTime locale={i18n.language} value={reflection?.generated_at} /></td>
              <td><AuditTime locale={i18n.language} value={reflection?.last_attempted_at} /></td>
            </tr>
            <tr>
              <th scope="row">{t("methodFeedback")}</th><td>{feedback?.status ?? "—"}</td>
              <td><AuditTime locale={i18n.language} value={feedback?.qualified_at} /></td>
              <td><AuditTime locale={i18n.language} value={feedback?.available_at} /></td>
              <td><AuditTime locale={i18n.language} value={feedback?.retired_at} /></td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  );
}

function AuditDetail({ detail }: { detail: ResearchReviewAuditDetail }) {
  const { t } = useTranslation();
  const review = detail.review;
  const provenanceRows: Array<[string, ReactNode]> = [
    [t("observationMethod"), review.outcome.method_category],
    [t("version"), review.outcome.method_version],
    [t("marketTimezone"), review.outcome.market_timezone],
    [t("priceSemantics"), review.outcome.price_semantics],
    [t("adjustmentSemantics"), review.outcome.adjustment_semantics],
    [t("horizonLimit"), review.outcome.horizon_limit],
    [t("qualificationPolicy"), review.outcome_feedback?.qualification_policy_version],
    [t("qualificationReasons"), review.outcome_feedback?.reasons.join(", ")],
    [t("limitations"), review.outcome.limitations.join(" · ")],
  ];
  return (
    <div className="review-audit-content">
      <AuditLifecycle detail={detail} />
      <section className="review-audit-section">
        <h3>{t("generationProvenance")}</h3>
        <KeyValueTable rows={provenanceRows} />
      </section>
      <section className="review-audit-section">
        <h3>{t("usageSummary")}</h3>
        <UsageTable attemptCount={detail.aggregate_usage.attempt_count} usage={detail.aggregate_usage} />
      </section>
      <AuditAttempts detail={detail} />
    </div>
  );
}

type UsageView = ResearchReviewAuditDetail["attempts"][number]["usage"];

function UsageTable({
  attemptCount,
  usage,
}: {
  attemptCount?: number;
  usage: UsageView;
}) {
  const { t, i18n } = useTranslation();
  const fields: Array<[string, number | string | null | undefined]> = [
    ...(attemptCount === undefined ? [] : [[t("attempts"), attemptCount] as [string, number]]),
    [t("usageStatus"), usage.usage_status],
    [t("llmCalls"), usage.llm_calls],
    [t("inputTokens"), usage.input_tokens],
    [t("outputTokens"), usage.output_tokens],
    [t("cacheHitInputTokens"), usage.cache_hit_input_tokens],
    [t("cacheMissInputTokens"), usage.cache_miss_input_tokens],
    [t("reasoningOutputTokens"), usage.reasoning_output_tokens],
    [t("wallTime"), usage.wall_time_seconds],
    [t("providerCostUsd"), usage.provider_reported_cost_usd],
  ];
  return (
    <div className="review-table-scroll">
      <table className="review-audit-table review-usage-table">
        <thead><tr>{fields.map(([label]) => <th key={label}>{label}</th>)}</tr></thead>
        <tbody><tr>{fields.map(([label, value]) => (
          <td key={label}>{formatAuditNumber(i18n.language, value)}</td>
        ))}</tr></tbody>
      </table>
    </div>
  );
}

function KeyValueTable({ rows }: { rows: Array<[string, ReactNode]> }) {
  return (
    <div className="review-table-scroll">
      <table className="review-audit-table review-key-value-table">
        <tbody>{rows.map(([label, value]) => (
          <tr key={label}><th scope="row">{label}</th><td>{value || "—"}</td></tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function AuditTime({ locale, value }: { locale: string; value: string | null | undefined }) {
  if (!value) return <>—</>;
  const date = new Date(value);
  const label = Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "short" }).format(date);
  return <time dateTime={value} title={value}>{label}</time>;
}

function formatAuditNumber(locale: string, value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  return typeof value === "number" ? new Intl.NumberFormat(locale, { maximumFractionDigits: 4 }).format(value) : value;
}

function DecisionDetails({ review }: { review: ResearchReview }) {
  const { t } = useTranslation();
  return (
    <div className="memory-decision-details-body">
      <div className="memory-scenarios">
        <h3>{t("scenarios")}</h3>
        <div className="memory-scenario-grid">
          {review.decision.scenarios.map((scenario) => (
            <div className={`memory-scenario scenario-${scenario.kind}`} key={scenario.kind}>
              <strong>{t(`${scenario.kind}Scenario`)}</strong>
              <Markdown>{scenario.outcome}</Markdown>
              {scenario.core_assumptions.length > 0 && (
                <div className="memory-decision-field">
                  <strong>{t("coreAssumptions")}</strong>
                  <ul>
                    {scenario.core_assumptions.map((assumption) => (
                      <li key={assumption}><Markdown>{assumption}</Markdown></li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
      <div className="memory-decision-grid">
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

function feedbackStateMessageKey(status: ResearchReview["review_status"]): string {
  return {
    awaiting_observation: "feedbackAwaitingObservation",
    observation_delayed: "feedbackAwaitingObservation",
    awaiting_reflection: "feedbackAwaitingReflection",
    reflection_retry_scheduled: "feedbackRetryScheduled",
    reflection_failed: "feedbackGenerationFailed",
    reflection_invalid: "feedbackGenerationFailed",
    feedback_available: "feedbackUnavailable",
    feedback_ineligible: "feedbackUnavailable",
    feedback_retired: "feedbackUnavailable",
    lifecycle_inconsistent: "feedbackUnavailable",
  }[status];
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
