import { useMemo } from "react";
import { formatResearchDate } from "../researchDate";
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
  const { t, i18n } = useTranslation();
  const groups = useMemo(
    () => buildRoleMetricGroups(metrics, events, artifacts),
    [artifacts, events, metrics],
  );
  const contexts = useMemo(() => contextMetricRows(events), [events]);

  const nodeMetric = (node: string, field: string) => {
    const value = metrics?.node_metrics?.[node]?.[field as keyof NonNullable<RunMetrics["node_metrics"]>[string]];
    return typeof value === "number" ? value.toLocaleString(i18n.language) : t("notRecorded");
  };
  const hasMetric = (nodes: { node: string }[], field: string) => nodes.some(row => typeof metrics?.node_metrics?.[row.node]?.[field as keyof NonNullable<RunMetrics["node_metrics"]>[string]] === "number");
  return (
    <section className="diagnostic-block run-metrics">
      <h2>{t("runMetricsAndDiagnostics")}</h2>
      <div className="run-metrics-body">
        <p className="metrics-observation-note">{t("observedUsageNote")}</p>
      <div className="metrics-strip">
        <Metric label={t("llmCalls")} value={metrics?.llm_calls} />
        <Metric label={t("toolCalls")} value={metrics?.tool_calls} />
        <Metric
          label={t("cumulativeActiveTime")}
          value={metrics?.wall_time_seconds == null ? undefined : `${metrics.wall_time_seconds.toFixed(1)}s`}
        />
        <Metric label={t("inputTokens")} value={metrics?.input_tokens} />
        <Metric
          label={t("cacheHitInputTokens")}
          value={metrics?.cache_hit_input_tokens}
        />
        <Metric
          label={t("cacheMissInputTokens")}
          value={metrics?.cache_miss_input_tokens}
        />
        <Metric label={t("outputTokens")} value={metrics?.output_tokens} />
        <Metric
          label={t("reasoningOutputTokens")}
          value={metrics?.reasoning_output_tokens}
        />
        <Metric
          label={t("tokenDetailCoverage")}
          value={metrics?.detailed_usage_calls == null || metrics.llm_calls == null ? undefined : tokenDetailCoverage(metrics.detailed_usage_calls, metrics.llm_calls)}
          help={t("tokenDetailCoverageDescription")}
        />

      </div>

      {groups.length > 0 && (
        <section className="role-metrics" aria-label={t("roleMetrics")}>
          <h3>{t("roleMetrics")}</h3><p className="secondary-line">{t("roleMetricsTimelineOrder")}</p>
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
                  <span>{hasMetric(group.nodes, "llm_calls") ? t("llmCallsCompact", { count: group.llmCalls }) : t("notRecorded")}</span>
                  <span>
                    {hasMetric(group.nodes, "input_tokens") ? t("inputCompact", { count: group.inputTokens.toLocaleString() }) : t("notRecorded")}
                  </span>
                  <span>
                    {hasMetric(group.nodes, "output_tokens") ? t("outputCompact", { count: group.outputTokens.toLocaleString() }) : t("notRecorded")}
                  </span>
                  <span>{hasMetric(group.nodes, "wall_time_seconds") ? `${group.activeTime.toFixed(1)}s` : t("notRecorded")}</span>
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
                            <td>{nodeMetric(row.node, "llm_calls")}</td>
                            <td>{nodeMetric(row.node, "tool_calls")}</td>
                            <td>{nodeMetric(row.node, "input_tokens")}</td>
                            <td>{nodeMetric(row.node, "cache_hit_input_tokens")}</td>
                            <td>{nodeMetric(row.node, "cache_miss_input_tokens")}</td>
                            <td>{nodeMetric(row.node, "output_tokens")}</td>
                            <td>{nodeMetric(row.node, "reasoning_output_tokens")}</td>
                            <td title={t("tokenDetailCoverageDescription")}>
                              {hasMetric([row], "detailed_usage_calls") && hasMetric([row], "llm_calls") ? tokenDetailCoverage(row.detailedUsageCalls, row.llmCalls) : t("notRecorded")}
                            </td>
                            <td>{hasMetric([row], "wall_time_seconds") ? `${row.activeTime.toFixed(1)}s` : t("notRecorded")}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              </details>
            ))}
          </div>
        </section>
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

      <section
        className="node-metrics attempt-metrics"
        aria-label={t("attemptMetrics")}
      >
        <h3>{t("attemptMetrics")}</h3>
        {attempts.length === 0 ? (
          <p className="metrics-empty">{t("noAttemptMetrics")}</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("attempt")}</th>
                  <th>{t("status")}</th>
                  <th>{t("startedAt")}</th><th>{t("finishedAt")}</th><th>{t("resumeCount")}</th>
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
                    <td>{formatResearchDate(attempt.started_at, i18n.language)}</td><td>{formatResearchDate(attempt.finished_at, i18n.language)}</td><td>{attempt.resume_count}</td>
                    <td>{attempt.error_code ?? "—"}</td>
                    <td>{attempt.metrics?.llm_calls?.toLocaleString() ?? t("notRecorded")}</td>
                    <td>{attempt.metrics?.tool_calls?.toLocaleString() ?? t("notRecorded")}</td>
                    <td>{attempt.metrics?.input_tokens?.toLocaleString() ?? t("notRecorded")}</td>
                    <td>{attempt.metrics?.output_tokens?.toLocaleString() ?? t("notRecorded")}</td>
                    <td>{attempt.metrics?.wall_time_seconds == null ? t("notRecorded") : `${attempt.metrics.wall_time_seconds.toFixed(1)}s`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      </div>
    </section>
  );
}

function Metric({
  label,
  value,
  help,
}: {
  label: string;
  value: number | string | undefined;
  help?: string;
}) {
  const { t } = useTranslation();
  return (
    <div title={help}>
      <span>{label}</span>
      <strong>{value == null ? t("notRecorded") : typeof value === "number" ? value.toLocaleString() : value}</strong>
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
