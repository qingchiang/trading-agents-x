import { useCallback, useEffect, useMemo, useState } from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import {
  api,
  type AnalystReport,
  type Capabilities,
  type DebateAgenda,
  type EvidenceBundle,
  type JudgeDraft,
  type RebuttalReview,
  type ResearchArtifact,
  type ResearchCase,
  type ResearchDecision,
  type RiskReview,
  type RunDetail as RunDetailType,
  type RunEvent,
  type RunMetrics,
} from "../api/client";
import Markdown from "../components/Markdown";
import {
  buildEvidenceReferenceIndex,
  groupEvidenceRefs,
  type EvidenceDisplayGroup,
  type EvidenceReferenceIndex,
} from "../evidence";
import StatusBadge from "../components/StatusBadge";
import {
  Link,
  useLocation,
  useNavigate,
  useParams,
} from "../router";
import { formatUtcDate, trashDeadline } from "../trash";

const terminal = new Set(["succeeded", "failed", "cancelled"]);
const reportOrder = ["fundamentals", "market", "news", "social"] as const;
const timelineOrderStorageKey = "tradingagents-timeline-order";
const auditDetailsStorageKey = "tradingagents-audit-details-open";
const eventNames = [
  "run.queued",
  "run.started",
  "run.resumed",
  "node.started",
  "node.completed",
  "node.output_retry",
  "node.output_recovered",
  "node.output_failed",
  "artifact.created",
  "run.succeeded",
  "run.failed",
  "run.cancelled",
  "run.cancel_requested",
  "run.retry_queued",
];
const viewNames = [
  "timeline",
  "deliberation",
  "evidence",
  "reports",
  "decision",
] as const;

type ViewName = (typeof viewNames)[number];
type ReturnViewName = Exclude<ViewName, "evidence">;
type TimelineOrder = "newest" | "oldest";
type ArtifactContent = ResearchArtifact["content"];
type VisibleWarning =
  | string
  | NonNullable<AnalystReport["warnings"]>[number];

export default function RunDetail() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const { runId = "" } = useParams();
  const [detail, setDetail] = useState<RunDetailType | null>(null);
  const [artifacts, setArtifacts] = useState<ResearchArtifact[]>([]);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [error, setError] = useState("");
  const searchParams = useMemo(
    () => new URLSearchParams(location.search),
    [location.search],
  );
  const requestedView = searchParams.get("view");
  const activeView: ViewName = isViewName(requestedView)
    ? requestedView
    : "timeline";
  const requestedReport = searchParams.get("report") ?? "";
  const focusedEvidence = searchParams.get("ref") ?? "";

  const refresh = useCallback(async () => {
    try {
      const [nextDetail, nextArtifacts] = await Promise.all([
        api.run(runId),
        api.artifacts(runId),
      ]);
      setDetail(nextDetail);
      setArtifacts(nextArtifacts);
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
        return [...current, event];
      });
      if (
        event.event_type === "node.completed" ||
        event.event_type === "artifact.created" ||
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
    eventNames.forEach((name) =>
      source.addEventListener(name, receive as EventListener),
    );
    source.onerror = () => {
      void refresh();
    };
    return () => source.close();
  }, [runId, refresh]);

  useEffect(() => {
    let active = true;
    void api
      .capabilities()
      .then((value) => {
        if (active) setCapabilities(value);
      })
      .catch(() => {
        if (active) setCapabilities(null);
      });
    return () => {
      active = false;
    };
  }, []);

  const reports = useMemo<Record<string, AnalystReport | string>>(() => {
    const completed = detail?.result?.reports ?? {};
    if (Object.keys(completed).length > 0) return completed;
    return Object.fromEntries(
      artifacts
        .filter(
          (artifact) =>
            artifact.stage === "analyst" &&
            isAnalystReport(artifact.content),
        )
        .map((artifact) => [
          artifact.role,
          artifact.content as AnalystReport,
        ]),
    );
  }, [artifacts, detail?.result?.reports]);
  const reportNames = useMemo(
    () => orderReportNames(Object.keys(reports)),
    [reports],
  );
  const activeReport = reportNames.includes(requestedReport)
    ? requestedReport
    : (reportNames[0] ?? "");
  const evidenceIndex = useMemo(
    () =>
      buildEvidenceReferenceIndex(
        detail?.result?.evidence ?? null,
      ),
    [detail?.result?.evidence],
  );
  const runWarnings = useMemo(() => {
    const reportWarningKeys = new Set(
      Object.values(reports)
        .flatMap(reportWarnings)
        .map(warningKey),
    );
    return dedupeWarnings(detail?.result?.warnings ?? []).filter(
      (warning) => !reportWarningKeys.has(warningKey(warning)),
    );
  }, [detail?.result?.warnings, reports]);

  useEffect(() => {
    if (
      activeView !== "reports" ||
      !activeReport ||
      requestedReport === activeReport
    ) {
      return;
    }
    navigate(
      runDetailPath(runId, {
        view: "reports",
        report: activeReport,
      }),
      { replace: true },
    );
  }, [activeReport, activeView, navigate, requestedReport, runId]);

  useEffect(() => {
    if (activeView !== "evidence" || !focusedEvidence) return;
    const targetRef =
      evidenceIndex.primaryRefs[focusedEvidence] ?? focusedEvidence;
    const target = document.getElementById(`evidence-${targetRef}`);
    target?.focus();
    target?.scrollIntoView?.({ behavior: "smooth", block: "center" });
  }, [
    activeView,
    detail?.result?.evidence?.digest,
    detail?.result?.evidence?.items.length,
    evidenceIndex.primaryRefs,
    focusedEvidence,
  ]);

  const selectView = useCallback(
    (view: ViewName) => {
      navigate(
        runDetailPath(runId, {
          view,
          report:
            view === "reports" && activeReport ? activeReport : undefined,
        }),
        { replace: true },
      );
    },
    [activeReport, navigate, runId],
  );

  const selectReport = useCallback(
    (report: string) => {
      navigate(
        runDetailPath(runId, {
          view: "reports",
          report,
        }),
        { replace: true },
      );
    },
    [navigate, runId],
  );

  const openEvidence = useCallback(
    (ref: string) => {
      navigate(
        runDetailPath(runId, {
          view: "evidence",
          ref,
          return_view:
            activeView === "evidence" ? "timeline" : activeView,
          return_report:
            activeView === "reports" && activeReport
              ? activeReport
              : undefined,
        }),
      );
    },
    [activeReport, activeView, navigate, runId],
  );

  const requestedReturnView = searchParams.get("return_view");
  const returnView: ReturnViewName = isReturnViewName(requestedReturnView)
    ? requestedReturnView
    : "timeline";
  const requestedReturnReport = searchParams.get("return_report") ?? "";
  const returnReport =
    returnView === "reports" && reportNames.includes(requestedReturnReport)
      ? requestedReturnReport
      : returnView === "reports"
        ? activeReport
        : "";
  const returnFromEvidence = useCallback(() => {
    navigate(
      runDetailPath(runId, {
        view: returnView,
        report: returnReport || undefined,
      }),
      { replace: true },
    );
  }, [navigate, returnReport, returnView, runId]);

  const act = async (action: "cancel" | "retry") => {
    try {
      await api.action(runId, action);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("error"));
    }
  };

  const restore = async () => {
    try {
      await api.restoreRuns([runId]);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("error"));
    }
  };

  if (!detail) {
    return <div className="loading">{error || t("loading")}</div>;
  }
  const { run, result } = detail;

  return (
    <section>
      <header className="page-header run-heading">
        <div>
          <Link className="back-link" to="/">
            ← {t("dashboard")}
          </Link>
          <div className="run-title">
            <h1>{run.request.ticker}</h1>
            {run.instrument_name && (
              <span className="run-instrument-name">{run.instrument_name}</span>
            )}
            <StatusBadge status={run.status} />
          </div>
          <p className="subtitle">
            {run.request.analysis_date} · {run.request.profile} · {t("attempt")}{" "}
            {run.attempt}
          </p>
          {run.source_run_id && (
            <p className="subtitle">
              {t("sourceRun")}:{" "}
              <Link to={`/runs/${encodeURIComponent(run.source_run_id)}`}>
                {run.source_run_id}
              </Link>
            </p>
          )}
        </div>
        <div className="action-row">
          {!run.trashed_at &&
            (run.status === "queued" || run.status === "running") && (
            <button className="button danger" onClick={() => void act("cancel")}>
              {t("cancel")}
            </button>
          )}
          {!run.trashed_at && run.status === "failed" && (
            <button className="button" onClick={() => void act("retry")}>
              {t("retry")}
            </button>
          )}
          {run.trashed_at && (
            <button className="button primary" onClick={() => void restore()}>
              {t("restore")}
            </button>
          )}
          {terminal.has(run.status) && (
            <Link
              className="button"
              to={`/runs/new?from_run=${encodeURIComponent(runId)}`}
            >
              {t("newFromRun")}
            </Link>
          )}
          <a
            className="button"
            href={`/api/v1/runs/${runId}/export?format=markdown`}
          >
            {t("exportMarkdown")}
          </a>
          <a
            className="button"
            href={`/api/v1/runs/${runId}/export?format=json`}
          >
            {t("exportJson")}
          </a>
        </div>
      </header>
      {error && <div className="alert">{error}</div>}
      {run.trashed_at && (
        <div className="trash-notice">
          <strong>{t("trashedRun")}</strong>
          <span>
            {cleanupLabel(
              run.trashed_at,
              capabilities?.defaults.trash_retention_days ?? 30,
              t,
            )}
          </span>
        </div>
      )}
      {run.error_message && <div className="alert">{run.error_message}</div>}
      <RunWarnings warnings={runWarnings} />

      <nav
        className="panel view-tabs"
        aria-label={t("researchViews")}
        role="tablist"
      >
        {viewNames.map((view) => (
          <button
            type="button"
            role="tab"
            aria-selected={activeView === view}
            aria-controls={`run-view-${view}`}
            className={activeView === view ? "active" : ""}
            onClick={() => selectView(view)}
            key={view}
          >
            {t(view)}
          </button>
        ))}
      </nav>

      {activeView === "timeline" && (
        <TimelinePanel events={events} />
      )}
      {activeView === "deliberation" && (
        <DeliberationPanel
          artifacts={artifacts}
          onEvidence={openEvidence}
          evidenceIndex={evidenceIndex}
        />
      )}
      {activeView === "evidence" && (
        <EvidencePanel
          evidence={result?.evidence ?? null}
          focusedRef={focusedEvidence}
          onReturn={returnFromEvidence}
          returnLabel={returnViewLabel(t, returnView)}
          evidenceIndex={evidenceIndex}
        />
      )}
      {activeView === "reports" && (
        <ReportsPanel
          reports={reports}
          reportNames={reportNames}
          activeReport={activeReport}
          onReport={selectReport}
          onEvidence={openEvidence}
          evidenceIndex={evidenceIndex}
        />
      )}
      {activeView === "decision" && (
        <DecisionPanel
          decision={result?.decision ?? null}
          onEvidence={openEvidence}
          evidenceIndex={evidenceIndex}
        />
      )}

      {result && <MetricsPanel metrics={result.metrics} />}
    </section>
  );
}

function TimelinePanel({ events }: { events: RunEvent[] }) {
  const { t } = useTranslation();
  const [order, setOrder] = useState<TimelineOrder>(readTimelineOrder);
  const orderedEvents = useMemo(
    () =>
      [...events].sort((left, right) =>
        order === "newest"
          ? right.sequence - left.sequence
          : left.sequence - right.sequence,
      ),
    [events, order],
  );

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
          <h2>{t("timeline")}</h2>
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
      <div className="timeline">
        {orderedEvents.map((event) => (
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
        {events.length === 0 && (
          <div className="empty-state">{t("waitingForEvents")}</div>
        )}
      </div>
    </article>
  );
}

function DeliberationPanel({
  artifacts,
  onEvidence,
  evidenceIndex,
}: {
  artifacts: ResearchArtifact[];
  onEvidence: (ref: string) => void;
  evidenceIndex: EvidenceReferenceIndex;
}) {
  const { t } = useTranslation();
  const deliberation = artifacts.filter(
    (artifact) =>
      artifact.stage !== "analyst" && artifact.stage !== "decision",
  );
  return (
    <article
      className="panel audit-panel"
      id="run-view-deliberation"
      role="tabpanel"
    >
      <div className="panel-header">
        <div>
          <p className="eyebrow">{t("researchArtifacts")}</p>
          <h2>{t("deliberation")}</h2>
        </div>
        <span className="event-count">{deliberation.length}</span>
      </div>
      {deliberation.length === 0 ? (
        <div className="empty-state">
          {artifacts.length === 0
            ? t("noArtifactsRecorded")
            : t("noDeliberation")}
        </div>
      ) : (
        <div className="artifact-list">
          {deliberation.map((artifact) => (
            <ArtifactCard
              artifact={artifact}
              onEvidence={onEvidence}
              evidenceIndex={evidenceIndex}
              key={artifact.id}
            />
          ))}
        </div>
      )}
    </article>
  );
}

function ArtifactCard({
  artifact,
  onEvidence,
  evidenceIndex,
}: {
  artifact: ResearchArtifact;
  onEvidence: (ref: string) => void;
  evidenceIndex: EvidenceReferenceIndex;
}) {
  const { t } = useTranslation();
  const content = artifact.content;
  const researchCase = isResearchCase(content) ? content : null;
  const agenda = isDebateAgenda(content) ? content : null;
  const rebuttal = isRebuttalReview(content) ? content : null;
  const judge = isJudgeDraft(content) ? content : null;
  const risk = isRiskReview(content) ? content : null;
  const decision = isResearchDecision(content) ? content : null;
  const refs = content.evidence_refs ?? [];
  const primaryText =
    researchCase?.thesis ??
    agenda?.executive_summary ??
    rebuttal?.thesis_update ??
    judge?.thesis ??
    risk?.executive_summary ??
    decision?.thesis ??
    "";
  return (
    <section className="artifact-card">
      <header className="artifact-header">
        <div>
          <span className="artifact-stage">{artifact.stage}</span>
          <h3>{artifact.role}</h3>
        </div>
        <small>
          {t("round")} {artifact.round} · {t("attempt")} {artifact.attempt}
          {" · "}
          {artifact.generation_method}
        </small>
      </header>
      <div className="artifact-body">
        <Markdown
          evidenceAliases={evidenceIndex.aliases}
          onEvidence={onEvidence}
        >
          {primaryText}
        </Markdown>
        {decision && (
          <p className="artifact-rating">
            <strong>{decision.rating}</strong> · {t("confidence")}{" "}
            {Math.round(decision.confidence * 100)}%
          </p>
        )}
        {researchCase && (
          <>
            <List
              title={t("claimRebuttals")}
              items={researchCase.strongest_counterarguments ?? []}
              evidenceIndex={evidenceIndex}
              onEvidence={onEvidence}
            />
            <List
              title={t("risks")}
              items={researchCase.risks ?? []}
              evidenceIndex={evidenceIndex}
              onEvidence={onEvidence}
            />
          </>
        )}
        {agenda && (
          <List
            title={t("claimRebuttals")}
            items={agenda.issues.map((issue) => issue.question)}
            evidenceIndex={evidenceIndex}
            onEvidence={onEvidence}
          />
        )}
        {rebuttal && (
          <List
            title={t("claimRebuttals")}
            items={rebuttal.responses.map((response) => response.response)}
            evidenceIndex={evidenceIndex}
            onEvidence={onEvidence}
          />
        )}
        {judge && (
          <List
            title={t("risks")}
            items={judge.risks}
            evidenceIndex={evidenceIndex}
            onEvidence={onEvidence}
          />
        )}
        {risk && (
          <List
            title={t("risks")}
            items={risk.findings.map((finding) => finding.statement)}
            evidenceIndex={evidenceIndex}
            onEvidence={onEvidence}
          />
        )}
        {decision && (
          <div className="decision-columns compact">
            <List
              title={t("catalysts")}
              items={decision.catalysts ?? []}
              emptyLabel={t("noCatalystsIdentified")}
              evidenceIndex={evidenceIndex}
              onEvidence={onEvidence}
            />
            <List
              title={t("risks")}
              items={decision.risks ?? []}
              evidenceIndex={evidenceIndex}
              onEvidence={onEvidence}
            />
            <List
              title={t("invalidation")}
              items={decision.invalidation_conditions ?? []}
              evidenceIndex={evidenceIndex}
              onEvidence={onEvidence}
            />
          </div>
        )}
        <EvidenceRefs
          refs={refs}
          onEvidence={onEvidence}
          evidenceIndex={evidenceIndex}
        />
        {rebuttal && (rebuttal.new_evidence_refs ?? []).length > 0 && (
          <div className="artifact-new-evidence">
            <strong>{t("newEvidenceRefs")}</strong>
            <EvidenceRefs
              refs={rebuttal.new_evidence_refs ?? []}
              onEvidence={onEvidence}
              evidenceIndex={evidenceIndex}
              hideLabel
            />
          </div>
        )}
      </div>
    </section>
  );
}

function EvidencePanel({
  evidence,
  focusedRef,
  onReturn,
  returnLabel,
  evidenceIndex,
}: {
  evidence: EvidenceBundle | null;
  focusedRef: string;
  onReturn: () => void;
  returnLabel: string;
  evidenceIndex: EvidenceReferenceIndex;
}) {
  const { t } = useTranslation();
  return (
    <article
      className="panel audit-panel"
      id="run-view-evidence"
      role="tabpanel"
    >
      <div className="panel-header">
        <div>
          <p className="eyebrow">{t("evidenceBundle")}</p>
          <h2>{t("evidence")}</h2>
        </div>
        <div className="evidence-panel-actions">
          <button
            type="button"
            className="button compact-button"
            onClick={onReturn}
          >
            ← {returnLabel}
          </button>
          <span className="event-count">{evidenceIndex.groups.length}</span>
        </div>
      </div>
      {!evidence ? (
        <div className="empty-state">{t("noEvidenceRecorded")}</div>
      ) : (
        <>
          <dl className="bundle-summary">
            <div>
              <dt>{t("evidenceDigest")}</dt>
              <dd>{evidence.digest ?? "—"}</dd>
            </div>
            <div>
              <dt>{t("analysisDate")}</dt>
              <dd>{evidence.analysis_date}</dd>
            </div>
            <div>
              <dt>{t("version")}</dt>
              <dd>{evidence.version ?? "1"}</dd>
            </div>
            <div>
              <dt>{t("displayedEvidence")}</dt>
              <dd>
                {t("evidenceBodiesSummary", {
                  groups: evidenceIndex.groups.length,
                  items: evidence.items.length,
                })}
              </dd>
            </div>
          </dl>
          <div className="evidence-list">
            {evidenceIndex.groups.map((group) => (
              <EvidenceCard
                group={group}
                focused={group.refs.includes(focusedRef)}
                key={group.alias}
              />
            ))}
          </div>
        </>
      )}
    </article>
  );
}

function EvidenceCard({
  group,
  focused,
}: {
  group: EvidenceDisplayGroup;
  focused: boolean;
}) {
  const { t } = useTranslation();
  const item = group.canonical;
  const hasValue = item.value !== null && item.value !== undefined;
  const requestedDates = uniqueStrings(
    group.items.map((entry) => entry.requested_date),
  );
  const effectiveDates = uniqueStrings(
    group.items.flatMap((entry) =>
      entry.effective_date ? [entry.effective_date] : [],
    ),
  );
  const availableDates = uniqueStrings(
    group.items.flatMap((entry) =>
      entry.available_at ? [entry.available_at] : [],
    ),
  );
  const auditRecords = group.items.map(
    ({ content: _content, ...entry }) => entry,
  );
  return (
    <section
      className={`evidence-card ${focused ? "focused" : ""}`}
      id={`evidence-${item.ref}`}
      data-evidence-ref={group.refs.join(" ")}
      tabIndex={-1}
    >
      <header>
        <div>
          <code title={group.refs.join("\n")}>{group.alias}</code>
          <h3>{group.evidenceTypes.join(" · ")}</h3>
        </div>
        <span className={`quality quality-${group.quality}`}>
          {group.quality}
        </span>
      </header>
      <dl className="evidence-metadata">
        <div>
          <dt>{t("source")}</dt>
          <dd>{group.sources.join(", ")}</dd>
        </div>
        <div>
          <dt>{t("requestedDate")}</dt>
          <dd>{requestedDates.join(", ")}</dd>
        </div>
        <div>
          <dt>{t("effectiveDate")}</dt>
          <dd>{effectiveDates.join(", ") || "—"}</dd>
        </div>
        <div>
          <dt>{t("availableAt")}</dt>
          <dd>{availableDates.join(", ") || "—"}</dd>
        </div>
        <div>
          <dt>{t("fallback")}</dt>
          <dd>{group.fallback ? t("yes") : t("no")}</dd>
        </div>
        {hasValue && (
          <div>
            <dt>{t("value")}</dt>
            <dd>
              {String(item.value)} {item.unit ?? ""}
            </dd>
          </div>
        )}
      </dl>
      {item.content && (
        <div className="evidence-content">
          <Markdown>{item.content}</Markdown>
        </div>
      )}
      <details className="provenance-details evidence-audit-details">
        <summary>{t("canonicalEvidenceAndProvenance")}</summary>
        <div className="canonical-ref-list">
          {group.refs.map((ref) => (
            <div key={ref}>
              <code>{ref}</code>
              <button
                type="button"
                className="copy-ref-button"
                title={ref}
                aria-label={t("copyEvidenceId", { ref })}
                onClick={() => void copyEvidenceRef(ref)}
              >
                {t("copy")}
              </button>
            </div>
          ))}
        </div>
        <pre>{JSON.stringify(auditRecords, null, 2)}</pre>
      </details>
    </section>
  );
}

function ReportsPanel({
  reports,
  reportNames,
  activeReport,
  onReport,
  onEvidence,
  evidenceIndex,
}: {
  reports: Record<string, AnalystReport | string>;
  reportNames: string[];
  activeReport: string;
  onReport: (report: string) => void;
  onEvidence: (ref: string) => void;
  evidenceIndex: EvidenceReferenceIndex;
}) {
  const { t } = useTranslation();
  return (
    <article
      className="panel audit-panel report-panel"
      id="run-view-reports"
      role="tabpanel"
    >
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
                onClick={() => onReport(name)}
                key={name}
              >
                {reportLabel(t, name)}
              </button>
            ))}
          </div>
          <Markdown
            evidenceAliases={evidenceIndex.aliases}
            onEvidence={onEvidence}
          >
            {reportNarrative(reports[activeReport])}
          </Markdown>
          <ReportMetadata
            report={reports[activeReport]}
            onEvidence={onEvidence}
            evidenceIndex={evidenceIndex}
          />
        </>
      )}
    </article>
  );
}

function DecisionPanel({
  decision,
  onEvidence,
  evidenceIndex,
}: {
  decision: ResearchDecision | null;
  onEvidence: (ref: string) => void;
  evidenceIndex: EvidenceReferenceIndex;
}) {
  const { t } = useTranslation();
  if (!decision) {
    return (
      <article
        className="panel audit-panel"
        id="run-view-decision"
        role="tabpanel"
      >
        <div className="empty-state">{t("noDecision")}</div>
      </article>
    );
  }
  return (
    <article
      className="panel audit-panel decision-panel"
      id="run-view-decision"
      role="tabpanel"
    >
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
          <Markdown
            evidenceAliases={evidenceIndex.aliases}
            onEvidence={onEvidence}
          >
            {decision.thesis}
          </Markdown>
        </div>
        <EvidenceRefs
          refs={decision.evidence_refs ?? []}
          onEvidence={onEvidence}
          evidenceIndex={evidenceIndex}
        />
        <MemoryRefs refs={decision.memory_refs ?? []} />
        <div className="decision-columns">
          <List
            title={t("catalysts")}
            items={decision.catalysts ?? []}
            emptyLabel={t("noCatalystsIdentified")}
            evidenceIndex={evidenceIndex}
            onEvidence={onEvidence}
          />
          <List
            title={t("risks")}
            items={decision.risks ?? []}
            evidenceIndex={evidenceIndex}
            onEvidence={onEvidence}
          />
          <List
            title={t("invalidation")}
            items={decision.invalidation_conditions ?? []}
            evidenceIndex={evidenceIndex}
            onEvidence={onEvidence}
          />
        </div>
        <p className="horizon">
          <strong>{t("horizon")}:</strong> {decision.time_horizon}
        </p>
      </div>
    </article>
  );
}

function reportNarrative(value: unknown): string {
  if (typeof value === "string") {
    return value;
  }
  if (value && typeof value === "object" && isAnalystReport(value as ArtifactContent)) {
    const report = value as AnalystReport;
    return [
      "## Executive Summary",
      report.executive_summary,
      ...(report.sections ?? []).flatMap((section) => [
        `## ${section.title}`,
        section.narrative,
      ]),
      "## Catalysts",
      ...listMarkdown(report.catalysts ?? []),
      "## Risks",
      ...listMarkdown(report.risks ?? []),
      "## Invalidation Conditions",
      ...listMarkdown(report.invalidation_conditions ?? []),
    ].join("\n\n");
  }
  return JSON.stringify(value, null, 2);
}

function listMarkdown(items: string[]): string[] {
  return items.length > 0
    ? items.map((item) => `- ${item}`)
    : ["- —"];
}

function ReportMetadata({
  report,
  onEvidence,
  evidenceIndex,
}: {
  report: unknown;
  onEvidence: (ref: string) => void;
  evidenceIndex: EvidenceReferenceIndex;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(readAuditDetailsOpen);
  if (!report || typeof report !== "object") return null;
  const typed = report as {
    warnings?: VisibleWarning[];
    evidence_refs?: string[];
  };
  const warnings = dedupeWarnings(typed.warnings ?? []);
  const refs = typed.evidence_refs ?? [];
  const groups = groupEvidenceRefs(refs, evidenceIndex)
    .map((refGroup) =>
      evidenceIndex.groups.find(
        (group) => group.alias === refGroup.alias,
      ),
    )
    .filter((group): group is EvidenceDisplayGroup => Boolean(group));
  if (!warnings.length && !groups.length) return null;
  return (
    <details
      className="report-metadata audit-details"
      open={open}
    >
      <summary
        onClick={(event) => {
          event.preventDefault();
          const next = !open;
          setOpen(next);
          persistAuditDetailsOpen(next);
        }}
      >
        <strong>{t("auditDetails")}</strong>
        <span>
          {t("auditSummary", {
            warnings: warnings.length,
            evidence: groups.length,
          })}
        </span>
      </summary>
      <div className="audit-details-body">
        {warnings.length > 0 && (
          <div>
            <strong>{t("warnings")}</strong>
            <ul>
              {warnings.map((warning) => (
                <li key={warningKey(warning)}>
                  {warningMessage(warning)}
                </li>
              ))}
            </ul>
          </div>
        )}
        {groups.length > 0 && (
          <div>
            <strong>{t("evidenceRefs")}</strong>
            <ul className="audit-evidence-list">
              {groups.map((group) => (
                <li key={group.alias}>
                  <button
                    type="button"
                    className="open-evidence-button"
                    onClick={() => onEvidence(group.canonical.ref)}
                    title={group.refs.join("\n")}
                    aria-label={`${t("openEvidence")} ${group.canonical.ref}`}
                  >
                    {group.alias}
                  </button>
                  <span>{group.sources.join(", ")}</span>
                  <small>
                    {group.quality}
                    {" · "}
                    {evidenceDates(group).join(", ") || "—"}
                    {group.fallback ? ` · ${t("fallback")}` : ""}
                  </small>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </details>
  );
}

function RunWarnings({ warnings }: { warnings: VisibleWarning[] }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(readAuditDetailsOpen);
  if (!warnings.length) return null;
  return (
    <details
      className="run-warning-details audit-details"
      open={open}
    >
      <summary
        onClick={(event) => {
          event.preventDefault();
          const next = !open;
          setOpen(next);
          persistAuditDetailsOpen(next);
        }}
      >
        <strong>{t("runWarnings")}</strong>
        <span>
          {t("warningCount", { count: warnings.length })}
        </span>
      </summary>
      <ul>
        {warnings.map((warning) => (
          <li key={warningKey(warning)}>{warningMessage(warning)}</li>
        ))}
      </ul>
    </details>
  );
}

function reportWarnings(report: AnalystReport | string): VisibleWarning[] {
  return typeof report === "string"
    ? []
    : [...(report.warnings ?? [])];
}

function warningMessage(warning: VisibleWarning): string {
  return typeof warning === "string" ? warning : warning.message;
}

function warningKey(warning: VisibleWarning): string {
  return typeof warning === "string"
    ? warning
    : [
        warning.code,
        warning.evidence_ref,
        warning.source,
        warning.message,
      ].join(":");
}

function dedupeWarnings(warnings: VisibleWarning[]): VisibleWarning[] {
  const seen = new Set<string>();
  return warnings.filter((warning) => {
    const key = warningKey(warning);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function evidenceDates(group: EvidenceDisplayGroup): string[] {
  return Array.from(
    new Set(
      group.items.flatMap((item) =>
        item.effective_date
          ? [item.effective_date]
          : item.requested_date
            ? [item.requested_date]
            : [],
      ),
    ),
  );
}

function readAuditDetailsOpen(): boolean {
  return localStorage.getItem(auditDetailsStorageKey) === "true";
}

function persistAuditDetailsOpen(open: boolean): void {
  localStorage.setItem(auditDetailsStorageKey, String(open));
}

function EvidenceRefs({
  refs,
  onEvidence,
  evidenceIndex,
  hideLabel = false,
}: {
  refs: string[];
  onEvidence: (ref: string) => void;
  evidenceIndex: EvidenceReferenceIndex;
  hideLabel?: boolean;
}) {
  const { t } = useTranslation();
  if (!refs.length) return null;
  const groupedRefs = groupEvidenceRefs(refs, evidenceIndex);
  return (
    <div className="evidence-ref-group">
      {!hideLabel && <strong>{t("evidenceRefs")}</strong>}
      <div className="evidence-chips">
        {groupedRefs.map((group) => (
          <span className="evidence-chip" key={group.alias}>
            <button
              type="button"
              className="open-evidence-button"
              onClick={() => onEvidence(group.targetRef)}
              aria-label={`${t("openEvidence")} ${group.targetRef}`}
              title={group.refs.join("\n")}
            >
              <code>{group.alias}</code>
            </button>
            <button
              type="button"
              className="copy-chip-button"
              onClick={() => void copyEvidenceRef(group.targetRef)}
              aria-label={t("copyEvidenceId", {
                ref: group.targetRef,
              })}
              title={group.targetRef}
            >
              ⧉
            </button>
          </span>
        ))}
      </div>
    </div>
  );
}

function MemoryRefs({ refs }: { refs: string[] }) {
  const { t } = useTranslation();
  if (!refs.length) return null;
  return (
    <div className="evidence-ref-group memory-ref-group">
      <strong>{t("memoryRefs")}</strong>
      <div className="evidence-chips">
        {refs.map((ref) => {
          const runId = ref.startsWith("memory:") ? ref.slice(7) : ref;
          const encoded = encodeURIComponent(runId);
          return (
            <a
              href={`/memory?q=${encoded}#memory-${encoded}`}
              aria-label={`${t("openMemory")} ${ref}`}
              key={ref}
            >
              <code>{ref}</code>
            </a>
          );
        })}
      </div>
    </div>
  );
}

function List({
  title,
  items,
  emptyLabel = "—",
  evidenceIndex,
  onEvidence,
}: {
  title: string;
  items: string[];
  emptyLabel?: string;
  evidenceIndex?: EvidenceReferenceIndex;
  onEvidence?: (ref: string) => void;
}) {
  return (
    <div className="artifact-list-section">
      <h3>{title}</h3>
      {items.length ? (
        <ul>
          {items.map((item) => (
            <li key={item}>
              {evidenceIndex && onEvidence ? (
                <Markdown
                  evidenceAliases={evidenceIndex.aliases}
                  onEvidence={onEvidence}
                >
                  {item}
                </Markdown>
              ) : (
                item
              )}
            </li>
          ))}
        </ul>
      ) : (
        <span className="muted">{emptyLabel}</span>
      )}
    </div>
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

function MetricsPanel({ metrics }: { metrics: RunMetrics | undefined }) {
  const { t } = useTranslation();
  const rows = useMemo(() => nodeMetricRows(metrics), [metrics]);
  return (
    <article className="panel run-metrics">
      <div className="metrics-strip">
        <Metric label={t("llmCalls")} value={metrics?.llm_calls ?? 0} />
        <Metric label={t("toolCalls")} value={metrics?.tool_calls ?? 0} />
        <Metric label={t("inputTokens")} value={metrics?.input_tokens ?? 0} />
        <Metric label={t("outputTokens")} value={metrics?.output_tokens ?? 0} />
        <Metric
          label={t("wallTime")}
          value={`${(metrics?.wall_time_seconds ?? 0).toFixed(1)}s`}
        />
      </div>
      {rows.length > 0 && (
        <details className="node-metrics">
          <summary>
            {t("nodeMetrics")} <span>{rows.length}</span>
          </summary>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("node")}</th>
                  <th>{t("llmCalls")}</th>
                  <th>{t("toolCalls")}</th>
                  <th>{t("inputTokens")}</th>
                  <th>{t("outputTokens")}</th>
                  <th>{t("wallTime")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.node}>
                    <td>
                      <code>{row.node}</code>
                    </td>
                    <td>{metricCell(row.llmCalls)}</td>
                    <td>{metricCell(row.toolCalls)}</td>
                    <td>{metricCell(row.inputTokens)}</td>
                    <td>{metricCell(row.outputTokens)}</td>
                    <td>{row.wallTime.toFixed(1)}s</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
    </article>
  );
}

type NodeMetricRow = {
  node: string;
  llmCalls: number | null;
  toolCalls: number | null;
  inputTokens: number | null;
  outputTokens: number | null;
  wallTime: number;
};

function nodeMetricRows(metrics: RunMetrics | undefined): NodeMetricRow[] {
  const nodeMetrics = metrics?.node_metrics ?? {};
  return Object.keys(nodeMetrics)
    .map((node) => {
      const usage = nodeMetrics[node];
      return {
        node,
        llmCalls: usage.llm_calls ?? 0,
        toolCalls: usage.tool_calls ?? 0,
        inputTokens: usage.input_tokens ?? 0,
        outputTokens: usage.output_tokens ?? 0,
        wallTime: usage.wall_time_seconds ?? 0,
      };
    })
    .sort(
      (left, right) =>
        right.wallTime - left.wallTime ||
        left.node.localeCompare(right.node),
    );
}

function metricCell(value: number | null): string {
  return value === null ? "—" : value.toLocaleString();
}

function isAnalystReport(content: ArtifactContent): content is AnalystReport {
  return (
    "analyst" in content &&
    "executive_summary" in content &&
    "sections" in content
  );
}

function isResearchCase(
  content: ArtifactContent,
): content is ResearchCase {
  return "role" in content && "arguments" in content;
}

function isDebateAgenda(
  content: ArtifactContent,
): content is DebateAgenda {
  return "issues" in content;
}

function isRebuttalReview(
  content: ArtifactContent,
): content is RebuttalReview {
  return "responses" in content && "thesis_update" in content;
}

function isJudgeDraft(content: ArtifactContent): content is JudgeDraft {
  return "preliminary_rating" in content && "rulings" in content;
}

function isRiskReview(content: ArtifactContent): content is RiskReview {
  return "findings" in content && "confidence_adjustment" in content;
}

function isResearchDecision(
  content: ArtifactContent,
): content is ResearchDecision {
  return "rating" in content && "time_horizon" in content;
}

function uniqueStrings(values: string[]): string[] {
  return Array.from(new Set(values));
}

async function copyEvidenceRef(ref: string) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(ref);
    }
  } catch {
    // Clipboard permission failures do not affect evidence navigation.
  }
}

function isViewName(value: string | null): value is ViewName {
  return value !== null && (viewNames as readonly string[]).includes(value);
}

function isReturnViewName(value: string | null): value is ReturnViewName {
  return isViewName(value) && value !== "evidence";
}

function orderReportNames(names: string[]): string[] {
  const known = reportOrder.filter((name) => names.includes(name));
  const extensions = names
    .filter((name) => !(reportOrder as readonly string[]).includes(name))
    .sort();
  return [...known, ...extensions];
}

function reportLabel(t: TFunction, name: string): string {
  const labels: Record<string, string> = {
    fundamentals: "fundamentalsAnalyst",
    market: "marketAnalyst",
    news: "newsAnalyst",
    social: "socialAnalyst",
  };
  return labels[name] ? t(labels[name]) : name;
}

function returnViewLabel(t: TFunction, view: ReturnViewName): string {
  const labels: Record<ReturnViewName, string> = {
    timeline: "returnToTimeline",
    deliberation: "returnToDeliberation",
    reports: "returnToReports",
    decision: "returnToDecision",
  };
  return t(labels[view]);
}

function runDetailPath(
  runId: string,
  values: {
    view?: ViewName;
    report?: string;
    ref?: string;
    return_view?: ReturnViewName;
    return_report?: string;
  },
): string {
  const params = new URLSearchParams();
  Object.entries(values).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  const query = params.toString();
  return `/runs/${encodeURIComponent(runId)}${query ? `?${query}` : ""}`;
}

function readTimelineOrder(): TimelineOrder {
  return localStorage.getItem(timelineOrderStorageKey) === "oldest"
    ? "oldest"
    : "newest";
}

function cleanupLabel(
  trashedAt: string,
  retentionDays: number,
  t: TFunction,
) {
  const deadline = trashDeadline(trashedAt, retentionDays);
  if (!deadline) return t("trashRetentionDisabled");
  return t("scheduledCleanup", {
    date: formatUtcDate(deadline.deletionAt),
    remaining: deadline.due
      ? t("trashCleanupDue")
      : t("trashDaysRemaining", { count: deadline.remainingDays }),
  });
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}
