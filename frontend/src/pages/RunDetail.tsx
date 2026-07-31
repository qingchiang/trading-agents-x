import { useCallback, useEffect, useMemo, useState } from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import {
  api,
  type AnalystReport,
  type Capabilities,
  type EvidenceBundle,
  type ResearchArtifact,
  type ResearchDecision,
  type RunDetail as RunDetailType,
  type RunEvent,
  type RunAttemptView,
  type RunMetrics,
} from "../api/client";
import AnalystReportView from "../components/AnalystReportView";
import DeliberationView from "../components/DeliberationView";
import EvidenceTableView from "../components/EvidenceTableView";
import Markdown from "../components/Markdown";
import ResearchDecisionView from "../components/ResearchDecisionView";
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
const eventNames = [
  "run.queued",
  "run.started",
  "run.resumed",
  "node.started",
  "node.completed",
  "phase.started",
  "phase.completed",
  "node.context_prepared",
  "evidence.sealed",
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
  const [evidence, setEvidence] = useState<EvidenceBundle | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [error, setError] = useState("");
  const [sourceDrawerRef, setSourceDrawerRef] = useState<string | null>(null);
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
      let nextEvidence = nextDetail.result?.evidence ?? null;
      let evidenceError = "";
      if (nextDetail.evidence_status.status === "sealed") {
        try {
          nextEvidence = await api.evidence(runId);
        } catch (cause) {
          evidenceError = cause instanceof Error ? cause.message : t("error");
        }
      }
      setDetail(nextDetail);
      setArtifacts(nextArtifacts);
      setEvidence(nextEvidence);
      setError(evidenceError);
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
        event.event_type === "evidence.sealed" ||
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
    () => buildEvidenceReferenceIndex(evidence),
    [evidence],
  );
  const decision = useMemo(
    () =>
      detail?.result?.decision ??
      latestResearchDecision(artifacts),
    [artifacts, detail?.result?.decision],
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
    evidence?.digest,
    evidence?.items.length,
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
  const openSourceDrawer = useCallback((ref: string) => {
    setSourceDrawerRef(ref);
  }, []);

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
  const hasPartialResearch =
    run.status !== "succeeded" &&
    (evidence !== null ||
      artifacts.length > 0 ||
      Object.keys(reports).length > 0 ||
      decision !== null);

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
            className="button primary"
            href={`/api/v1/runs/${runId}/export?format=package`}
          >
            {t("exportPackage")}
          </a>
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
      {hasPartialResearch && (
        <div className="notice partial-research-notice" role="status">
          {t("partialResearchAvailable")}
        </div>
      )}
      <RunWarnings warnings={runWarnings} key={runId} />

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
          onEvidence={openSourceDrawer}
          evidenceIndex={evidenceIndex}
        />
      )}
      {activeView === "evidence" && (
        <EvidencePanel
          evidence={evidence}
          evidenceStatus={detail.evidence_status.status}
          runStatus={run.status}
          focusedRef={focusedEvidence}
          onReturn={returnFromEvidence}
          returnLabel={returnViewLabel(t, returnView)}
          evidenceIndex={evidenceIndex}
          onEvidence={openEvidence}
        />
      )}
      {activeView === "reports" && (
        <ReportsPanel
          reports={reports}
          reportNames={reportNames}
          activeReport={activeReport}
          onReport={selectReport}
          onEvidence={openSourceDrawer}
          evidenceIndex={evidenceIndex}
        />
      )}
      {activeView === "decision" && (
        <DecisionPanel
          decision={decision}
          onEvidence={openSourceDrawer}
          evidenceIndex={evidenceIndex}
        />
      )}

      <MetricsPanel
        metrics={run.metrics}
        attempts={detail.attempts ?? []}
        events={events}
      />
      <EvidenceSourceDrawer
        evidenceRef={sourceDrawerRef}
        evidenceIndex={evidenceIndex}
        onClose={() => setSourceDrawerRef(null)}
        key={sourceDrawerRef ?? "closed"}
      />
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
    (artifact) => artifact.stage !== "analyst",
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
      <DeliberationView
        artifacts={artifacts}
        onEvidence={onEvidence}
        evidenceIndex={evidenceIndex}
      />
    </article>
  );
}

function EvidencePanel({
  evidence,
  evidenceStatus,
  runStatus,
  focusedRef,
  onReturn,
  returnLabel,
  evidenceIndex,
  onEvidence,
}: {
  evidence: EvidenceBundle | null;
  evidenceStatus: RunDetailType["evidence_status"]["status"];
  runStatus: RunDetailType["run"]["status"];
  focusedRef: string;
  onReturn: () => void;
  returnLabel: string;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
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
        <div className="empty-state">
          {evidenceStatus === "pending" &&
          (runStatus === "queued" || runStatus === "running")
            ? t("evidencePending")
            : t("noEvidenceRecorded")}
        </div>
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
          {(evidence.tables ?? []).length > 0 && (
            <section className="evidence-table-list">
              <h3>{t("rawEvidenceTables")}</h3>
              {(evidence.tables ?? []).map((table) => (
                <EvidenceTableView
                  table={table}
                  evidenceIndex={evidenceIndex}
                  onEvidence={onEvidence}
                  key={table.id}
                />
              ))}
            </section>
          )}
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

function EvidenceSourceDrawer({
  evidenceRef,
  evidenceIndex,
  onClose,
}: {
  evidenceRef: string | null;
  evidenceIndex: EvidenceReferenceIndex;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const group = evidenceRef
    ? evidenceIndex.groups.find((candidate) =>
        candidate.refs.includes(evidenceRef),
      )
    : undefined;

  useEffect(() => {
    if (!evidenceRef) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [evidenceRef, onClose]);

  if (!evidenceRef) return null;
  return (
    <div className="source-drawer-layer" role="presentation">
      <button
        type="button"
        className="source-drawer-backdrop"
        aria-label={t("closeSourceDetails")}
        onClick={onClose}
      />
      <aside
        className="source-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={t("sourceDetails")}
      >
        <header>
          <div>
            <p className="eyebrow">{t("sourceDetails")}</p>
            <h2>{group?.sources.join(", ") || t("unknownSource")}</h2>
          </div>
          <button type="button" className="button" onClick={onClose}>
            {t("close")}
          </button>
        </header>
        {!group ? (
          <div className="empty-state">{t("evidenceReferenceUnavailable")}</div>
        ) : (
          <>
            <dl className="evidence-metadata">
              <div>
                <dt>{t("quality")}</dt>
                <dd>{group.quality}</dd>
              </div>
              <div>
                <dt>{t("effectiveDate")}</dt>
                <dd>{evidenceDates(group).join(", ") || "—"}</dd>
              </div>
              <div>
                <dt>{t("fallback")}</dt>
                <dd>{group.fallback ? t("yes") : t("no")}</dd>
              </div>
            </dl>
            {group.canonical.content && (
              <div className="source-drawer-content">
                <Markdown>{group.canonical.content}</Markdown>
              </div>
            )}
            {group.canonical.value !== null &&
              group.canonical.value !== undefined && (
                <p className="source-drawer-value">
                  <strong>{t("value")}:</strong>{" "}
                  {String(group.canonical.value)}{" "}
                  {group.canonical.unit ?? ""}
                </p>
              )}
            <details className="provenance-details">
              <summary>{t("canonicalEvidenceAndProvenance")}</summary>
              <div className="canonical-ref-list">
                {group.refs.map((ref) => (
                  <div key={ref}>
                    <code>{ref}</code>
                    <button
                      type="button"
                      className="copy-ref-button"
                      onClick={() => void copyEvidenceRef(ref)}
                    >
                      {t("copy")}
                    </button>
                  </div>
                ))}
              </div>
              <pre>
                {JSON.stringify(
                  group.items.map(({ content: _content, ...item }) => item),
                  null,
                  2,
                )}
              </pre>
            </details>
          </>
        )}
      </aside>
    </div>
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
          <AnalystReportView
            report={reports[activeReport]}
            evidenceIndex={evidenceIndex}
            onEvidence={onEvidence}
          />
          <ReportMetadata
            report={reports[activeReport]}
            onEvidence={onEvidence}
            evidenceIndex={evidenceIndex}
            key={activeReport}
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
  return (
    <ResearchDecisionView
      decision={decision}
      onEvidence={onEvidence}
      evidenceIndex={evidenceIndex}
    />
  );
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
  const [open, setOpen] = useState(false);
  if (!report || typeof report !== "object") return null;
  const typed = report as {
    warnings?: VisibleWarning[];
    source_refs?: string[];
  };
  const warnings = dedupeWarnings(typed.warnings ?? []);
  const refs = typed.source_refs ?? [];
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
            <ul className="audit-evidence-grid">
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
                  <span className="audit-source-name">
                    {group.sources.join(", ")}
                  </span>
                  <span className={`quality quality-${group.quality}`}>
                    {group.quality}
                  </span>
                  {group.fallback && (
                    <span className="audit-fallback-chip">{t("fallback")}</span>
                  )}
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
  const [open, setOpen] = useState(false);
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

function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{typeof value === "number" ? value.toLocaleString() : value}</strong>
    </div>
  );
}

function MetricsPanel({
  metrics,
  attempts,
  events,
}: {
  metrics: RunMetrics | undefined;
  attempts: RunAttemptView[];
  events: RunEvent[];
}) {
  const { t } = useTranslation();
  const rows = useMemo(
    () => nodeMetricRows(metrics, events),
    [events, metrics],
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
          label={t("wallTime")}
          value={`${(metrics?.wall_time_seconds ?? 0).toFixed(1)}s`}
        />
      </div>
      {rows.length > 0 && (
        <details className="node-metrics">
          <summary>
            {t("nodeMetrics")} <span>{rows.length}</span>
          </summary>
          <p className="metrics-observation-note">
            {t("nodeMetricsTimelineOrder")}
          </p>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("node")}</th>
                  <th>{t("phase")}</th>
                  <th>{t("llmCalls")}</th>
                  <th>{t("toolCalls")}</th>
                  <th>{t("inputTokens")}</th>
                  <th>{t("cacheHitInputTokens")}</th>
                  <th>{t("cacheMissInputTokens")}</th>
                  <th>{t("outputTokens")}</th>
                  <th>{t("reasoningOutputTokens")}</th>
                  <th>{t("detailedUsageCalls")}</th>
                  <th>{t("outputStatus")}</th>
                  <th>{t("wallTime")}</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.node}>
                    <td>
                      <code>{row.node}</code>
                    </td>
                    <td title={t(phaseDescriptionKey(row.phase))}>
                      {t(phaseLabelKey(row.phase))}
                    </td>
                    <td>{metricCell(row.llmCalls)}</td>
                    <td>{metricCell(row.toolCalls)}</td>
                    <td>{metricCell(row.inputTokens)}</td>
                    <td>{metricCell(row.cacheHitInputTokens)}</td>
                    <td>{metricCell(row.cacheMissInputTokens)}</td>
                    <td>{metricCell(row.outputTokens)}</td>
                    <td>{metricCell(row.reasoningOutputTokens)}</td>
                    <td>{metricCell(row.detailedUsageCalls)}</td>
                    <td>{t(outputStatusKey(row.outputStatus))}</td>
                    <td>{row.wallTime.toFixed(1)}s</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
      {contexts.length > 0 && (
        <details className="node-metrics context-metrics">
          <summary>
            {t("contextMetrics")} <span>{contexts.length}</span>
          </summary>
          <p className="metrics-observation-note">
            {t("contextMetricsDescription")}
          </p>
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
                    <td>
                      <code>{row.node}</code>
                    </td>
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
        <summary>
          {t("attemptMetrics")} <span>{attempts.length}</span>
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
                  <th>{t("wallTime")}</th>
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

function runStatusKey(status: RunAttemptView["status"]): string {
  return `status${status[0].toUpperCase()}${status.slice(1)}`;
}

type NodeMetricRow = {
  node: string;
  phase: MetricPhase;
  llmCalls: number | null;
  toolCalls: number | null;
  inputTokens: number | null;
  cacheHitInputTokens: number | null;
  cacheMissInputTokens: number | null;
  outputTokens: number | null;
  reasoningOutputTokens: number | null;
  detailedUsageCalls: number | null;
  outputStatus: OutputStatus;
  wallTime: number;
};

type MetricPhase =
  | "collect"
  | "context"
  | "write"
  | "audit"
  | "serialize"
  | "other";

type OutputStatus =
  | "normal"
  | "retry"
  | "recovered"
  | "auditIncomplete"
  | "failed";

type ContextMetricRow = {
  sequence: number;
  node: string;
  inlineCharacters: number;
  referenceCount: number;
  tableSummaryCount: number;
  catalogItems: number;
};

function nodeMetricRows(
  metrics: RunMetrics | undefined,
  events: RunEvent[],
): NodeMetricRow[] {
  const nodeMetrics = metrics?.node_metrics ?? {};
  const firstSequence = new Map<string, number>();
  for (const event of events) {
    if (
      event.node &&
      (event.event_type === "phase.started" ||
        event.event_type === "node.started") &&
      !firstSequence.has(event.node)
    ) {
      firstSequence.set(event.node, event.sequence);
    }
  }
  return Object.keys(nodeMetrics)
    .map((node) => {
      const usage = nodeMetrics[node];
      return {
        node,
        phase: metricPhase(node),
        llmCalls: usage.llm_calls ?? 0,
        toolCalls: usage.tool_calls ?? 0,
        inputTokens: usage.input_tokens ?? 0,
        cacheHitInputTokens: usage.cache_hit_input_tokens ?? 0,
        cacheMissInputTokens: usage.cache_miss_input_tokens ?? 0,
        outputTokens: usage.output_tokens ?? 0,
        reasoningOutputTokens: usage.reasoning_output_tokens ?? 0,
        detailedUsageCalls: usage.detailed_usage_calls ?? 0,
        outputStatus: nodeOutputStatus(node, events),
        wallTime: usage.wall_time_seconds ?? 0,
      };
    })
    .sort(
      (left, right) =>
        (firstSequence.get(left.node) ?? Number.MAX_SAFE_INTEGER) -
          (firstSequence.get(right.node) ?? Number.MAX_SAFE_INTEGER) ||
        left.node.localeCompare(right.node),
    );
}

function metricPhase(node: string): MetricPhase {
  if (node.endsWith(".context")) return "context";
  if (
    node.endsWith(".report") ||
    node.endsWith(".write") ||
    node.endsWith(".reason")
  ) {
    return "write";
  }
  if (node.endsWith(".audit")) return "audit";
  if (node.endsWith(".serialize")) return "serialize";
  if (
    node.endsWith(".collect") ||
    /^analyst\.[^.]+$/.test(node)
  ) {
    return "collect";
  }
  return "other";
}

function nodeOutputStatus(node: string, events: RunEvent[]): OutputStatus {
  const outputEvents = events.filter((event) => event.node === node);
  if (
    outputEvents.some((event) => event.event_type === "node.output_recovered")
  ) {
    return "recovered";
  }
  if (outputEvents.some((event) => event.event_type === "node.output_failed")) {
    return node.endsWith(".audit") ? "auditIncomplete" : "failed";
  }
  if (outputEvents.some((event) => event.event_type === "node.output_retry")) {
    return "retry";
  }
  return "normal";
}

function outputStatusKey(status: OutputStatus): string {
  return `outputStatus${status[0].toUpperCase()}${status.slice(1)}`;
}

function contextMetricRows(events: RunEvent[]): ContextMetricRow[] {
  return events
    .filter(
      (event) =>
        event.event_type === "node.context_prepared" &&
        typeof event.node === "string",
    )
    .map((event) => ({
      sequence: event.sequence,
      node: event.node ?? "context",
      inlineCharacters: numericPayload(event.payload, "inline_characters"),
      referenceCount: numericPayload(event.payload, "reference_count"),
      tableSummaryCount: numericPayload(event.payload, "table_summary_count"),
      catalogItems: numericPayload(event.payload, "catalog_items"),
    }))
    .sort((left, right) => left.sequence - right.sequence);
}

function numericPayload(
  payload: Record<string, unknown> | undefined,
  key: string,
): number {
  const value = payload?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function phaseLabelKey(phase: MetricPhase): string {
  return `phase${phase[0].toUpperCase()}${phase.slice(1)}`;
}

function phaseDescriptionKey(phase: MetricPhase): string {
  return `${phaseLabelKey(phase)}Description`;
}

function metricCell(value: number | null): string {
  return value === null ? "—" : value.toLocaleString();
}

function isAnalystReport(content: ArtifactContent): content is AnalystReport {
  return (
    "analyst" in content &&
    "markdown" in content &&
    "report_sections" in content &&
    "audit_status" in content
  );
}

function isResearchDecision(
  content: ArtifactContent,
): content is ResearchDecision {
  return (
    "rating" in content &&
    "thesis" in content &&
    "scenarios" in content
  );
}

function latestResearchDecision(
  artifacts: ResearchArtifact[],
): ResearchDecision | null {
  for (const artifact of [...artifacts].reverse()) {
    if (
      artifact.stage === "decision" &&
      isResearchDecision(artifact.content)
    ) {
      return artifact.content;
    }
  }
  return null;
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
