import { FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  api,
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
  const [auditDetails, setAuditDetails] = useState<
    Record<number, ResearchReviewAuditDetail>
  >({});
  const recentInstruments = useRecentInstruments();

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
    target?.scrollIntoView?.({ behavior: "smooth", block: "center" });
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
  const regenerateReflection = async (outcomeId: number) => {
    setActionOutcomeId(outcomeId);
    setReflectionActionError("");
    setReflectionActionErrorOutcomeId(null);
    try {
      await api.regenerateOutcomeReflection(outcomeId, crypto.randomUUID());
      await load(window.location.search);
    } catch (cause) {
      setReflectionActionError(cause instanceof Error ? cause.message : t("error"));
      setReflectionActionErrorOutcomeId(outcomeId);
    } finally {
      setActionOutcomeId(null);
    }
  };
  const retireFeedback = async (feedbackId: number, outcomeId: number) => {
    setActionOutcomeId(outcomeId);
    try {
      await api.retireOutcomeFeedback(feedbackId, "retired_by_user");
      await load(window.location.search);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("error"));
    } finally {
      setActionOutcomeId(null);
    }
  };
  const loadAuditDetail = async (outcomeId: number) => {
    if (auditDetails[outcomeId]) return;
    try {
      const detail = await api.reviewAuditDetail(outcomeId);
      setAuditDetails((current) => ({ ...current, [outcomeId]: detail }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("error"));
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
                    <button
                      className="button compact-button"
                      disabled={actionOutcomeId === review.outcome_id}
                      onClick={() =>
                        void retireFeedback(
                          review.outcome_feedback!.id,
                          review.outcome_id,
                        )
                      }
                      type="button"
                    >
                      {t("retireFeedback")}
                    </button>
                  )}
                </>
              ) : review.review_status === "feedback_ineligible" ? (
                <p>
                  {t("feedbackIneligible")}: {review.outcome_feedback?.reasons.join(", ") || "—"}
                </p>
              ) : review.review_status === "feedback_retired" ? (
                <p>{t("feedbackRetired")}</p>
              ) : (
                <p>{t("feedbackUnavailable")}</p>
              )}
            </section>
            {review.lifecycle_actions_allowed &&
            ["reflection_invalid", "reflection_failed"].includes(
              review.review_status,
            ) ? (
              <section aria-labelledby={`reflection-failure-${review.outcome_id}`}>
                <h2 id={`reflection-failure-${review.outcome_id}`}>
                  {t("methodReflection")}
                </h2>
                <p>
                  {review.outcome_reflection?.error_code || t("reflectionFailure")}
                </p>
                {reflectionActionError && reflectionActionErrorOutcomeId === review.outcome_id && (
                  <div className="alert" role="alert">{reflectionActionError}</div>
                )}
                <button
                  className="button compact-button"
                  disabled={actionOutcomeId === review.outcome_id}
                  onClick={() => void regenerateReflection(review.outcome_id)}
                  type="button"
                >
                  {t("regenerateReflection")}
                </button>
              </section>
            ) : review.outcome_reflection?.generation_cycle?.status === "queued" ? (
              <section aria-live="polite">
                <h2>{t("methodReflection")}</h2>
                <p>{t("reflectionQueued")}</p>
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
    </section>
  );
}

function AuditAttempts({ detail }: { detail: ResearchReviewAuditDetail }) {
  return (
    <div className="memory-lifecycle-note">
      <p>
        {detail.aggregate_usage.attempt_count} attempt(s) · {detail.aggregate_usage.usage_status}
      </p>
      <pre>{JSON.stringify(detail.aggregate_usage, null, 2)}</pre>
      {detail.attempts.map((attempt) => (
        <details key={attempt.id}>
          <summary>
            #{attempt.sequence} · {attempt.attempt_kind} · {attempt.outcome ?? "running"}
          </summary>
          <p>{attempt.schema_version ?? "—"}</p>
          <p>
            {attempt.started_at} · {attempt.finished_at ?? "running"}
          </p>
          <p>{JSON.stringify(attempt.diagnostics ?? {})}</p>
          <p>{attempt.validation_issues?.join(", ") ?? "—"}</p>
          {attempt.invalid_candidate && <pre>{attempt.invalid_candidate}</pre>}
        </details>
      ))}
    </div>
  );
}

function DecisionDetails({ review }: { review: ResearchReview }) {
  const { t } = useTranslation();
  return (
    <div className="memory-decision-details-body">
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
