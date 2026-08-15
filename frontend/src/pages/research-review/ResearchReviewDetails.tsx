import { type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  type ResearchReview,
  type ResearchReviewAuditDetail,
} from "../../api/client";
import Markdown from "../../components/Markdown";

export function ReflectionAnalysis({ reflection }: { reflection: string }) {
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

export function ReviewDetailDisclosure({
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
              [t("generationCycleId"), attempt.generation_cycle_id],
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
  const cycle = reflection?.generation_cycle;
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
            {cycle && (
              <tr>
                <th scope="row">{t("generationCycle")}</th><td>{cycle.status}</td>
                <td><AuditTime locale={i18n.language} value={cycle.queued_at} /></td>
                <td><AuditTime locale={i18n.language} value={cycle.due_at} /></td>
                <td>—</td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <div className="review-audit-subsection">
        <h4>{t("lifecycleDiagnostics")}</h4>
        <KeyValueTable
          rows={[
            [
              t("nextObservationCheck"),
              <AuditTime locale={i18n.language} value={outcome.next_check_at} />,
            ],
            [t("observationError"), outcome.error_message],
            [
              t("nextReflectionRetry"),
              <AuditTime locale={i18n.language} value={reflection?.next_retry_at} />,
            ],
            [t("reflectionError"), reflection?.error_code],
            [t("generationCycleId"), cycle?.id],
            [t("retryOrdinal"), cycle?.retry_ordinal],
          ]}
        />
      </div>
    </section>
  );
}

export function AuditDetail({ detail }: { detail: ResearchReviewAuditDetail }) {
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
          <tr key={label}>
            <th scope="row">{label}</th>
            <td>{value === null || value === undefined || value === "" ? "—" : value}</td>
          </tr>
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

export function DecisionDetails({ review }: { review: ResearchReview }) {
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

export function percent(value: number | null) {
  return value == null ? "—" : `${value >= 0 ? "+" : ""}${(value * 100).toFixed(1)}%`;
}

export function feedbackStateMessageKey(status: ResearchReview["review_status"]): string {
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

export function runDecisionPath(runId: string): string {
  return `/runs/${encodeURIComponent(runId)}?view=decision`;
}

export function profileDescriptionKey(profile: ResearchReview["profile"]): string {
  return {
    fast: "profileFastDesc",
    standard: "profileStandardDesc",
    deep: "profileDeepDesc",
  }[profile];
}
