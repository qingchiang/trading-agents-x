import { useTranslation } from "react-i18next";
import {
  InstrumentIdentity,
  instrumentAccessibleName,
} from "../../components/Instruments";
import Markdown from "../../components/Markdown";
import StatusBadge from "../../components/StatusBadge";
import { Link } from "../../router";
import {
  AuditDetail,
  DecisionDetails,
  ReflectionAnalysis,
  ReviewDetailDisclosure,
  feedbackStateMessageKey,
  percent,
  profileDescriptionKey,
  runDecisionPath,
} from "./ResearchReviewDetails";
import { ResearchReviewFilters } from "./ResearchReviewFilters";
import { type ResearchReviewPage } from "./useResearchReviewPage";

export function ResearchReviewView({ actions, model }: ResearchReviewPage) {
  const { t } = useTranslation();
  const {
    actionAnnouncement,
    actionOutcomeId,
    auditDetails,
    error,
    filter: filterModel,
    reflectionActionError,
    reflectionActionErrorOutcomeId,
    retirement,
    retirementError,
    retirementNote,
    retirementReason,
    reviews,
  } = model;
  const {
    changeRetirementNote,
    changeRetirementReason,
    closeRetirement,
    filter: filterActions,
    loadAuditDetail,
    openRetirement,
    regenerateReflection,
    registerRetirementReason,
    registerReview,
    retireFeedback,
  } = actions;
  return (
    <section>
      <header className="page-header">
        <div>
          <p className="eyebrow">{t("deterministicFeedback")}</p>
          <h1>{t("researchReview")}</h1>
          <p className="subtitle">{t("researchReviewHint")}</p>
        </div>
      </header>
      <ResearchReviewFilters actions={filterActions} model={filterModel} />
      {error && <div className="alert">{error}</div>}
      <p
        aria-live="polite"
        className="review-action-announcement"
        role="status"
      >
        {actionAnnouncement}
      </p>
      <div className="memory-list">
        {reviews.map((review) => (
          <article
            className="panel memory-card"
            id={`review-${review.run_id}`}
            key={review.run_id}
            ref={(element) => {
              registerReview(review.outcome_id, element);
            }}
            tabIndex={-1}
          >
            <div className="memory-meta">
              <div>
                <Link
                  className="memory-title-link"
                  to={runDecisionPath(review.run_id)}
                  aria-label={`${t("openResearchDecision")} ${instrumentAccessibleName(
                    review.ticker,
                    review.instrument_local_name,
                    review.instrument_name,
                  )}`}
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
                  {t(
                    review.review_status === "reflection_invalid"
                      ? "reflectionInvalid"
                      : "reflectionFailure",
                  )}
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
                ref={registerRetirementReason}
                onChange={(event) =>
                  changeRetirementReason(event.target.value)
                }
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
                onChange={(event) => changeRetirementNote(event.target.value)}
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
