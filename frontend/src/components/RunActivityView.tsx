import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import type { RunEvent, RunDetail as RunDetailType } from "../api/client";
import { aggregateRunActivity, type ActivityAction, type ActivitySignal, type ActivityStage, type ActivityState } from "../runActivity";
const timelineOrderStorageKey = "tradingagents-timeline-order";
type TimelineOrder = "newest" | "oldest";
export default function RunActivityView({
  events,
  researchKind,
  currentAttempt,
  runStatus,
  elapsedSeconds,
}: {
  events: RunEvent[];
  researchKind: "full" | "incremental";
  currentAttempt: number;
  elapsedSeconds?: number;
  runStatus: RunDetailType["run"]["status"];
}) {
  const { t } = useTranslation();
  const [order, setOrder] = useState<TimelineOrder>(readTimelineOrder);
  const attempts = useMemo(
    () => aggregateRunActivity(events, researchKind, { currentAttempt, runStatus }),
    [currentAttempt, events, researchKind, runStatus],
  );
  const latest = attempts[0];
  const stages = researchKind === "incremental"
    ? (["collection", "incremental_semantic", "incremental_serialization", "commit"] as ActivityStage[])
    : (["collection", "analyst_reports", "research_cases", "debate", "research_judgment", "risk_review", "final_decision", "commit"] as ActivityStage[]);
  const updateOrder = (next: TimelineOrder) => {
    setOrder(next);
    localStorage.setItem(timelineOrderStorageKey, next);
  };
  return (
    <article
      className="panel audit-panel timeline-panel"
      id="run-view-timeline"
      role="tabpanel"
    >
      <div className="panel-header">
        <div>
          <p className="eyebrow">{t("liveEvents")}</p>
          <h2>{t("activity")}</h2>
        </div>
        <div className="timeline-controls">
          <div
            className="timeline-order"
            role="group"
            aria-label={t("timelineOrder")}
          >
            <button
              type="button"
              className={order === "newest" ? "active" : ""}
              aria-pressed={order === "newest"}
              onClick={() => updateOrder("newest")}
            >
              {t("latestFirst")}
            </button>
            <button
              type="button"
              className={order === "oldest" ? "active" : ""}
              aria-pressed={order === "oldest"}
              onClick={() => updateOrder("oldest")}
            >
              {t("earliestFirst")}
            </button>
          </div>
          <span className="event-count">{events.length}</span>
        </div>
      </div>
      <div className="queue-strip activity-timing">
        <span>{t("recordedDuration")}: {elapsedSeconds == null ? "—" : `${Math.round(elapsedSeconds)} s`}</span>
        <span>{t("recentActivity")}: {events.length ? formatTime(events.at(-1)?.created_at ?? "") : "—"}</span>
      </div>
      {latest && (
        <div className="activity-stage-overview">
          <div className="activity-live-summary" aria-live="polite" aria-atomic="true">
            <span>{t("currentResearchStage")}</span>
            <strong>{t(activityStageLabel(latest.currentStage))}</strong>
            <small>{t(activityStateLabel(latest.state))}</small>
          </div>
          <ol className="activity-stage-track" aria-label={t("researchProgress") }>
            {stages.map((stage) => {
              const state = latest.stageStates[stage] ?? "pending";
              return (
                <li className={state} key={stage}>
                  <span aria-hidden="true" />
                  <small>{t(activityStageLabel(stage))}</small>
                </li>
              );
            })}
          </ol>
        </div>
      )}
      <div className="activity-attempts">
        {attempts.map((attempt, attemptIndex) => (
          <details className={`activity-attempt ${attempt.state}`} open={attemptIndex === 0} key={attempt.attempt}>
            <summary>
              <span>{t("researchAttempt", { count: attempt.attempt })}</span>
              <span className={`activity-state ${attempt.state}`}>{t(activityStateLabel(attempt.state))}</span>
            </summary>
            <div
              className="activity-attempt-body"
              tabIndex={0}
              aria-label={t("attemptActivityLog", { count: attempt.attempt })}
            >
              <div className="activity-work-units">
                {[...attempt.workUnits]
                  .sort((left, right) =>
                    order === "newest"
                      ? right.lastSequence - left.lastSequence
                      : left.firstSequence - right.firstSequence,
                  )
                  .map((unit) => {
                    return (
                      <article className={`activity-work-unit ${unit.state}`} key={unit.key}>
                        <span className="activity-work-marker" aria-hidden="true" />
                        <div>
                          <strong>
                            {t(activityStageLabel(unit.stage))}
                            {unit.role ? ` · ${activityRoleLabel(t, unit.role)}` : ""}
                            {` · ${t(activityActionLabel(unit.action))}`}
                          </strong>
                          <div className="activity-unit-statuses">
                            <span className={`activity-state ${unit.state}`}>
                              {t(activityStateLabel(unit.state))}
                            </span>
                            {unit.signals
                              .filter((signal) => signal !== unit.state)
                              .map((signal) => (
                                <span className={`activity-state ${signal}`} key={signal}>
                                  {t(activitySignalLabel(signal))}
                                </span>
                              ))}
                          </div>
                          <small>
                            {formatTime(unit.events.at(-1)?.created_at ?? "")}
                          </small>

                        </div>
                      </article>
                    );
                  })}
              </div>
            </div>
          </details>
        ))}
        {events.length === 0 && (
          <div className="empty-state">{t("waitingForEvents")}</div>
        )}
      </div>
    </article>
  );
}

function readTimelineOrder(): TimelineOrder {
  return localStorage.getItem(timelineOrderStorageKey) === "oldest"
    ? "oldest"
    : "newest";
}

function activityStageLabel(stage: ActivityStage): string {
  return `activityStage_${stage}`;
}

function activityStateLabel(state: ActivityState): string {
  return `activityState_${state}`;
}

function activityActionLabel(action: ActivityAction): string {
  return `activityAction_${action}`;
}

function activitySignalLabel(signal: ActivitySignal): string {
  return `activitySignal_${signal}`;
}

function activityRoleLabel(t: TFunction, role: string): string {
  const analystKey = `${role}Analyst`;
  return ["market", "social", "news", "fundamentals"].includes(role)
    ? t(analystKey)
    : t(`activityRole_${role}`);
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}
