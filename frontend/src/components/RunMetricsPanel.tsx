import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import type {
  ResearchArtifact,
  RunAttemptView,
  RunEvent,
  RunMetrics,
} from "../api/client";
import {
  buildRoleMetricGroups,
  contextMetricRows,
  tokenDetailCoverage,
  type MetricPhase,
  type OutputStatus,
} from "../runMetrics";

export default function RunMetricsPanel({
  metrics,
  attempts,
  events,
  artifacts,
}: {
  metrics: RunMetrics | undefined;
  attempts: RunAttemptView[];
  events: RunEvent[];
  artifacts: ResearchArtifact[];
}) {
  const { t } = useTranslation();
  const groups = useMemo(
    () => buildRoleMetricGroups(metrics, events, artifacts),
    [artifacts, events, metrics],
  );
  const contexts = useMemo(() => contextMetricRows(events), [events]);

  return (
    <details className="panel run-metrics run-metrics-disclosure">
      <summary>
        <strong>{t("runMetricsAndDiagnostics")}</strong>
        <span>
          {t("runMetricsCompactSummary", {
            llm: metrics?.llm_calls ?? 0,
            input: (metrics?.input_tokens ?? 0).toLocaleString(),
            output: (metrics?.output_tokens ?? 0).toLocaleString(),
            seconds: (metrics?.wall_time_seconds ?? 0).toFixed(1),
          })}
        </span>
      </summary>
      <div className="run-metrics-body">
        <p className="metrics-observation-note">{t("observedUsageNote")}</p>
      <div className="metrics-strip">
        <Metric label={t("llmCalls")} value={metrics?.llm_calls ?? 0} />
        <Metric label={t("toolCalls")} value={metrics?.tool_calls ?? 0} />
        <Metric label={t("inputTokens")} value={metrics?.input_tokens ?? 0} />
        <Metric
          label={t("cacheHitInputTokens")}
          value={metrics?.cache_hit_input_tokens ?? 0}
        />
        <Metric
          label={t("cacheMissInputTokens")}
          value={metrics?.cache_miss_input_tokens ?? 0}
        />
        <Metric label={t("outputTokens")} value={metrics?.output_tokens ?? 0} />
        <Metric
          label={t("reasoningOutputTokens")}
          value={metrics?.reasoning_output_tokens ?? 0}
        />
        <Metric
          label={t("tokenDetailCoverage")}
          value={tokenDetailCoverage(
            metrics?.detailed_usage_calls ?? 0,
            metrics?.llm_calls ?? 0,
          )}
          help={t("tokenDetailCoverageDescription")}
        />
        <Metric
          label={t("cumulativeActiveTime")}
          value={`${(metrics?.wall_time_seconds ?? 0).toFixed(1)}s`}
        />
      </div>

      {groups.length > 0 && (
        <details className="role-metrics" aria-label={t("roleMetrics")}>
          <summary className="metric-section-summary">
            <span className="metric-summary-title">
              <span className="metric-disclosure-arrow" aria-hidden="true">›</span>
              {t("roleMetrics")}
              <span className="metric-count">{groups.length}</span>
            </span>
            <span className="metric-summary-description">
              {t("roleMetricsTimelineOrder")}
            </span>
          </summary>
          <div className="role-metric-list">
            {groups.map((group) => (
              <details
                aria-label={t(group.labelKey)}
                className="role-metric-group"
                key={group.id}
              >
                <summary>
                  <span className="role-metric-name">
                    <span
                      className="metric-disclosure-arrow"
                      aria-hidden="true"
                    >
                      ›
                    </span>
                    {t(group.labelKey)}
                  </span>
                  <span>{t(outputStatusKey(group.outputStatus))}</span>
                  <span>{t("llmCallsCompact", { count: group.llmCalls })}</span>
                  <span>
                    {t("inputCompact", {
                      count: group.inputTokens.toLocaleString(),
                    })}
                  </span>
                  <span>
                    {t("outputCompact", {
                      count: group.outputTokens.toLocaleString(),
                    })}
                  </span>
                  <span>{group.activeTime.toFixed(1)}s</span>
                </summary>
                <div className="table-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th rowSpan={2}>{t("node")}</th>
                        <th rowSpan={2}>{t("responsibility")}</th>
                        <th rowSpan={2}>{t("structuredTask")}</th>
                        <th rowSpan={2}>{t("clientRole")}</th>
                        <th rowSpan={2}>{t("generationMethod")}</th>
                        <th rowSpan={2}>{t("outputStatus")}</th>
                        <th colSpan={2}>{t("calls")}</th>
                        <th colSpan={3}>{t("inputBreakdown")}</th>
                        <th colSpan={2}>{t("outputBreakdown")}</th>
                        <th
                          rowSpan={2}
                          title={t("tokenDetailCoverageDescription")}
                        >
                          {t("tokenDetailCoverage")}
                        </th>
                        <th rowSpan={2}>{t("cumulativeActiveTime")}</th>
                      </tr>
                      <tr>
                        <th>{t("llm")}</th>
                        <th>{t("tools")}</th>
                        <th>{t("total")}</th>
                        <th>{t("cacheHit")}</th>
                        <th>{t("cacheMiss")}</th>
                        <th>{t("total")}</th>
                        <th>{t("reasoningIncluded")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {group.nodes.map((row) => {
                        const observation = row.observations.at(-1);
                        return (
                          <tr key={row.node}>
                            <td><code>{row.node}</code></td>
                            <td title={t(phaseDescriptionKey(row.phase))}>
                              {t(phaseLabelKey(row.phase))}
                            </td>
                            <td>
                              {observation
                                ? t(`taskKind.${observation.task_kind}`)
                                : t("notRecorded")}
                            </td>
                            <td>
                              {observation
                                ? t(`clientRoleName.${observation.client_role}`)
                                : t("notRecorded")}
                            </td>
                            <td>
                              {observation?.generation_method ??
                                t("notRecorded")}
                            </td>
                            <td>{t(outputStatusKey(row.outputStatus))}</td>
                            <td>{row.llmCalls.toLocaleString()}</td>
                            <td>{row.toolCalls.toLocaleString()}</td>
                            <td>{row.inputTokens.toLocaleString()}</td>
                            <td>{row.cacheHitInputTokens.toLocaleString()}</td>
                            <td>{row.cacheMissInputTokens.toLocaleString()}</td>
                            <td>{row.outputTokens.toLocaleString()}</td>
                            <td>{row.reasoningOutputTokens.toLocaleString()}</td>
                            <td title={t("tokenDetailCoverageDescription")}>
                              {tokenDetailCoverage(
                                row.detailedUsageCalls,
                                row.llmCalls,
                              )}
                            </td>
                            <td>{row.activeTime.toFixed(1)}s</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </details>
            ))}
          </div>
        </details>
      )}

      {contexts.length > 0 && (
        <details
          className="node-metrics context-metrics"
          aria-label={t("contextMetrics")}
        >
          <summary className="metric-section-summary">
            <span className="metric-summary-title">
              <span className="metric-disclosure-arrow" aria-hidden="true">
                ›
              </span>
              {t("contextMetrics")}
              <span className="metric-count">{contexts.length}</span>
            </span>
            <span className="metric-summary-description">
              {t("contextMetricsDescription")}
            </span>
          </summary>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("node")}</th>
                  <th>{t("contextCharacters")}</th>
                  <th>{t("evidenceReferences")}</th>
                  <th>{t("tableSummaries")}</th>
                  <th>{t("catalogItems")}</th>
                </tr>
              </thead>
              <tbody>
                {contexts.map((row) => (
                  <tr key={`${row.sequence}:${row.node}`}>
                    <td><code>{row.node}</code></td>
                    <td>{row.inlineCharacters.toLocaleString()}</td>
                    <td>{row.referenceCount.toLocaleString()}</td>
                    <td>{row.tableSummaryCount.toLocaleString()}</td>
                    <td>{row.catalogItems.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}

      <details
        className="node-metrics attempt-metrics"
        aria-label={t("attemptMetrics")}
      >
        <summary className="metric-section-summary">
          <span className="metric-summary-title">
            <span className="metric-disclosure-arrow" aria-hidden="true">
              ›
            </span>
            {t("attemptMetrics")}
            <span className="metric-count">{attempts.length}</span>
          </span>
        </summary>
        {attempts.length === 0 ? (
          <p className="metrics-empty">{t("noAttemptMetrics")}</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("attempt")}</th>
                  <th>{t("status")}</th>
                  <th>{t("resumeCount")}</th>
                  <th>{t("errorType")}</th>
                  <th>{t("llmCalls")}</th>
                  <th>{t("toolCalls")}</th>
                  <th>{t("inputTokens")}</th>
                  <th>{t("outputTokens")}</th>
                  <th>{t("cumulativeActiveTime")}</th>
                </tr>
              </thead>
              <tbody>
                {attempts.map((attempt) => (
                  <tr key={attempt.attempt}>
                    <td>{attempt.attempt}</td>
                    <td>{t(runStatusKey(attempt.status))}</td>
                    <td>{attempt.resume_count}</td>
                    <td>{attempt.error_code ?? "—"}</td>
                    <td>{(attempt.metrics?.llm_calls ?? 0).toLocaleString()}</td>
                    <td>{(attempt.metrics?.tool_calls ?? 0).toLocaleString()}</td>
                    <td>{(attempt.metrics?.input_tokens ?? 0).toLocaleString()}</td>
                    <td>{(attempt.metrics?.output_tokens ?? 0).toLocaleString()}</td>
                    <td>{(attempt.metrics?.wall_time_seconds ?? 0).toFixed(1)}s</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </details>
      </div>
    </details>
  );
}

function Metric({
  label,
  value,
  help,
}: {
  label: string;
  value: number | string;
  help?: string;
}) {
  return (
    <div title={help}>
      <span>{label}</span>
      <strong>{typeof value === "number" ? value.toLocaleString() : value}</strong>
    </div>
  );
}

function runStatusKey(status: RunAttemptView["status"]): string {
  return `status${status[0].toUpperCase()}${status.slice(1)}`;
}

function outputStatusKey(status: OutputStatus): string {
  return `outputStatus${status[0].toUpperCase()}${status.slice(1)}`;
}

function phaseLabelKey(phase: MetricPhase): string {
  return `phase${phase[0].toUpperCase()}${phase.slice(1)}`;
}

function phaseDescriptionKey(phase: MetricPhase): string {
  return `${phaseLabelKey(phase)}Description`;
}
