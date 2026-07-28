import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  api,
  type RunDetail as RunDetailType,
  type RunEvent,
} from "../api/client";
import Markdown from "../components/Markdown";
import StatusBadge from "../components/StatusBadge";
import { Link, useNavigate, useParams } from "../router";

const terminal = new Set(["succeeded", "failed", "cancelled"]);
const eventNames = [
  "run.queued",
  "run.started",
  "run.resumed",
  "node.started",
  "node.completed",
  "run.succeeded",
  "run.failed",
  "run.cancelled",
  "run.cancel_requested",
  "run.retry_queued",
];

export default function RunDetail() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { runId = "" } = useParams();
  const [detail, setDetail] = useState<RunDetailType | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [activeReport, setActiveReport] = useState("");
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      const next = await api.run(runId);
      setDetail(next);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("error"));
    }
  }, [runId, t]);

  useEffect(() => {
    void refresh();
    const source = new EventSource(`/api/v1/runs/${runId}/events`);
    const receive = (raw: MessageEvent<string>) => {
      let event: RunEvent;
      try {
        event = JSON.parse(raw.data) as RunEvent;
      } catch {
        return;
      }
      setEvents((current) => {
        if (current.some((item) => item.sequence === event.sequence)) {
          return current;
        }
        return [...current, event].sort((a, b) => a.sequence - b.sequence);
      });
      if (
        event.event_type === "node.completed" ||
        event.event_type.startsWith("run.")
      ) {
        void refresh();
      }
      if (
        event.event_type === "run.succeeded" ||
        event.event_type === "run.failed" ||
        event.event_type === "run.cancelled"
      ) {
        source.close();
      }
    };
    eventNames.forEach((name) => source.addEventListener(name, receive as EventListener));
    source.onerror = () => {
      void refresh();
    };
    return () => source.close();
  }, [runId, refresh]);

  const reports = useMemo(
    () => detail?.result?.reports ?? {},
    [detail?.result?.reports],
  );
  const reportNames = Object.keys(reports);
  useEffect(() => {
    if (!activeReport && reportNames.length) setActiveReport(reportNames[0]);
  }, [activeReport, reportNames]);

  const act = async (action: "cancel" | "retry" | "rerun") => {
    try {
      const next = await api.action(runId, action);
      if (action === "rerun") navigate(`/runs/${next.id}`);
      else await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("error"));
    }
  };

  if (!detail) {
    return <div className="loading">{error || t("loading")}</div>;
  }
  const { run, result } = detail;
  const decision = result?.decision;

  return (
    <section>
      <header className="page-header run-heading">
        <div>
          <Link className="back-link" to="/">
            ← {t("dashboard")}
          </Link>
          <div className="run-title">
            <h1>{run.request.ticker}</h1>
            <StatusBadge status={run.status} />
          </div>
          <p className="subtitle">
            {run.request.analysis_date} · {run.request.profile} · {t("attempt")}{" "}
            {run.attempt}
          </p>
        </div>
        <div className="action-row">
          {(run.status === "queued" || run.status === "running") && (
            <button className="button danger" onClick={() => void act("cancel")}>
              {t("cancel")}
            </button>
          )}
          {run.status === "failed" && (
            <button className="button" onClick={() => void act("retry")}>
              {t("retry")}
            </button>
          )}
          {terminal.has(run.status) && (
            <button className="button" onClick={() => void act("rerun")}>
              {t("rerun")}
            </button>
          )}
          <a className="button" href={`/api/v1/runs/${runId}/export?format=markdown`}>
            {t("exportMarkdown")}
          </a>
          <a className="button" href={`/api/v1/runs/${runId}/export?format=json`}>
            {t("exportJson")}
          </a>
        </div>
      </header>
      {error && <div className="alert">{error}</div>}
      {run.error_message && <div className="alert">{run.error_message}</div>}
      <div className="detail-grid">
        <article className="panel timeline-panel">
          <div className="panel-header">
            <div>
              <p className="eyebrow">{t("liveEvents")}</p>
              <h2>{t("timeline")}</h2>
            </div>
            <span className="event-count">{events.length}</span>
          </div>
          <div className="timeline">
            {events.map((event) => (
              <div className="timeline-item" key={event.sequence}>
                <span className="timeline-dot" />
                <div>
                  <strong>{event.node || event.event_type}</strong>
                  <small>
                    #{event.sequence} · {formatTime(event.created_at)}
                  </small>
                  {Object.keys(event.payload ?? {}).length > 0 && (
                    <code>{JSON.stringify(event.payload ?? {})}</code>
                  )}
                </div>
              </div>
            ))}
            {events.length === 0 && <div className="empty-state">{t("loading")}</div>}
          </div>
        </article>
        <article className="panel report-panel">
          <div className="panel-header">
            <div>
              <p className="eyebrow">{t("researchArtifacts")}</p>
              <h2>{t("reports")}</h2>
            </div>
          </div>
          {reportNames.length === 0 ? (
            <div className="empty-state">{t("noReports")}</div>
          ) : (
            <>
              <div className="tabs">
                {reportNames.map((name) => (
                  <button
                    className={activeReport === name ? "active" : ""}
                    onClick={() => setActiveReport(name)}
                    key={name}
                  >
                    {name}
                  </button>
                ))}
              </div>
              <Markdown>{reportNarrative(reports[activeReport])}</Markdown>
              <ReportMetadata
                report={reports[activeReport]}
                warningsLabel={t("warnings")}
                evidenceLabel={t("evidenceRefs")}
              />
            </>
          )}
        </article>
      </div>
      {decision && (
        <article className="panel decision-panel">
          <div className="decision-rating">
            <span>{t("researchRating")}</span>
            <strong>{decision.rating}</strong>
            <small>
              {t("confidence")} {Math.round(decision.confidence * 100)}%
            </small>
          </div>
          <div className="decision-body">
            <h2>{t("decision")}</h2>
            <h3>{t("thesis")}</h3>
            <div className="decision-thesis">
              <Markdown>{decision.thesis}</Markdown>
            </div>
            <div className="decision-columns">
              <List title={t("catalysts")} items={decision.catalysts ?? []} />
              <List title={t("risks")} items={decision.risks ?? []} />
              <List
                title={t("invalidation")}
                items={decision.invalidation_conditions ?? []}
              />
            </div>
            <p className="horizon">
              <strong>{t("horizon")}:</strong> {decision.time_horizon}
            </p>
          </div>
        </article>
      )}
      {result && (
        <article className="panel metrics-strip">
          <Metric label={t("llmCalls")} value={result.metrics?.llm_calls ?? 0} />
          <Metric label={t("toolCalls")} value={result.metrics?.tool_calls ?? 0} />
          <Metric
            label={t("inputTokens")}
            value={result.metrics?.input_tokens ?? 0}
          />
          <Metric
            label={t("outputTokens")}
            value={result.metrics?.output_tokens ?? 0}
          />
          <Metric
            label={t("wallTime")}
            value={`${(result.metrics?.wall_time_seconds ?? 0).toFixed(1)}s`}
          />
        </article>
      )}
    </section>
  );
}

function reportNarrative(value: unknown): string {
  if (typeof value === "string") return value;
  if (value && typeof value === "object" && "narrative" in value) {
    return String((value as { narrative: unknown }).narrative);
  }
  return JSON.stringify(value, null, 2);
}

function ReportMetadata({
  report,
  warningsLabel,
  evidenceLabel,
}: {
  report: unknown;
  warningsLabel: string;
  evidenceLabel: string;
}) {
  if (!report || typeof report !== "object") return null;
  const typed = report as {
    warnings?: string[];
    evidence_refs?: string[];
  };
  const warnings = typed.warnings ?? [];
  const refs = typed.evidence_refs ?? [];
  if (!warnings.length && !refs.length) return null;
  return (
    <div className="report-metadata">
      {warnings.length > 0 && (
        <div>
          <strong>{warningsLabel}</strong>
          <ul>
            {warnings.map((warning) => (
              <li key={warning}>{warning}</li>
            ))}
          </ul>
        </div>
      )}
      {refs.length > 0 && (
        <div>
          <strong>{evidenceLabel}</strong>
          <div className="evidence-chips">
            {refs.map((ref) => (
              <code key={ref}>{ref}</code>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function List({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <h3>{title}</h3>
      {items.length ? (
        <ul>{items.map((item) => <li key={item}>{item}</li>)}</ul>
      ) : (
        <span className="muted">—</span>
      )}
    </div>
  );
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{typeof value === "number" ? value.toLocaleString() : value}</strong>
    </div>
  );
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}
