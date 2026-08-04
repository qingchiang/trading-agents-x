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
    <article className="panel run-metrics">
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
          label={t("detailedUsageCoverage")}
          value={`${metrics?.detailed_usage_calls ?? 0}/${metrics?.llm_calls ?? 0}`}
        />
        <Metric
          label={t("cumulativeActiveTime")}
          value={`${(metrics?.wall_time_seconds ?? 0).toFixed(1)}s`}
        />
      </div>

      {groups.length > 0 && (
        <section className="role-metrics" aria-label={t("roleMetrics")}>
          <div className="role-metrics-heading">
            <strong>{t("roleMetrics")}</strong>
            <span>{t("roleMetricsTimelineOrder")}</span>
          </div>
          {groups.map((group) => (
            <details className="role-metric-group" key={group.id}>
              <summary>
                <span className="role-metric-name">{t(group.labelKey)}</span>
                <span>{t(outputStatusKey(group.outputStatus))}</span>
                <span>{t("callsCompact", { count: group.llmCalls })}</span>
                <span>{t("tokensCompact", { count: group.inputTokens + group.outputTokens })}</span>
                <span>{t("reasoningCompact", { count: group.reasoningOutputTokens })}</span>
                <span>{group.activeTime.toFixed(1)}s</span>
              </summary>
              <div className="role-metric-totals">
                <Metric label={t("toolCalls")} value={group.toolCalls} />
                <Metric label={t("cacheHitInputTokens")} value={group.cacheHitInputTokens} />
                <Metric label={t("cacheMissInputTokens")} value={group.cacheMissInputTokens} />
                <Metric label={t("detailedUsageCalls")} value={group.detailedUsageCalls} />
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>{t("node")}</th>
                      <th>{t("responsibility")}</th>
                      <th>{t("structuredTask")}</th>
                      <th>{t("clientRole")}</th>
                      <th>{t("generationMethod")}</th>
                      <th>{t("outputStatus")}</th>
                      <th>{t("llmCalls")}</th>
                      <th>{t("tokens")}</th>
                      <th>{t("cacheUsage")}</th>
                      <th>{t("reasoningOutputTokens")}</th>
                      <th>{t("cumulativeActiveTime")}</th>
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
                            {observation ? t(`taskKind.${observation.task_kind}`) : t("notRecorded")}
                          </td>
                          <td>
                            {observation ? t(`clientRoleName.${observation.client_role}`) : t("notRecorded")}
                          </td>
                          <td>
                            {observation?.generation_method ?? t("notRecorded")}
                          </td>
                          <td>{t(outputStatusKey(row.outputStatus))}</td>
                          <td>{row.llmCalls.toLocaleString()}</td>
                          <td>{(row.inputTokens + row.outputTokens).toLocaleString()}</td>
                          <td>{`${row.cacheHitInputTokens.toLocaleString()} / ${row.cacheMissInputTokens.toLocaleString()}`}</td>
                          <td>{row.reasoningOutputTokens.toLocaleString()}</td>
                          <td>{row.activeTime.toFixed(1)}s</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </details>
          ))}
        </section>
      )}

      {contexts.length > 0 && (
        <details className="node-metrics context-metrics">
          <summary>{t("contextMetrics")} <span>{contexts.length}</span></summary>
          <p className="metrics-observation-note">{t("contextMetricsDescription")}</p>
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

      <details className="node-metrics attempt-metrics">
        <summary>{t("attemptMetrics")} <span>{attempts.length}</span></summary>
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
    </article>
  );
}

function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <div>
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
