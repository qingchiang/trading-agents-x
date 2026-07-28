import { useCallback, useEffect, useMemo, useState } from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import {
  api,
  type AnalystReport,
  type ArtifactDiagnostics,
  type EvidenceBundle,
  type PerspectiveReview,
  type ResearchArtifact,
  type ResearchDecision,
  type RunDetail as RunDetailType,
  type RunEvent,
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

const terminal = new Set(["succeeded", "failed", "cancelled"]);
const reportOrder = ["fundamentals", "market", "news", "social"] as const;
const timelineOrderStorageKey = "tradingagents-timeline-order";
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

export default function RunDetail() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const { runId = "" } = useParams();
  const [detail, setDetail] = useState<RunDetailType | null>(null);
  const [artifacts, setArtifacts] = useState<ResearchArtifact[]>([]);
  const [events, setEvents] = useState<RunEvent[]>([]);
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
  const decisionDiagnostics = useMemo(
    () =>
      [...artifacts]
        .reverse()
        .find(
          (artifact) =>
            artifact.stage === "decision" &&
            artifact.attempt === detail?.run.attempt,
        )?.diagnostics ?? null,
    [artifacts, detail?.run.attempt],
  );

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
      {run.error_message && <div className="alert">{run.error_message}</div>}

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
          diagnostics={decisionDiagnostics}
          onEvidence={openEvidence}
          evidenceIndex={evidenceIndex}
        />
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
  const perspective = isPerspectiveReview(content) ? content : null;
  const decision = isResearchDecision(content) ? content : null;
  const diagnostics = artifact.diagnostics;
  const parsed = diagnostics?.parsed_thesis ?? null;
  const refs = content.evidence_refs ?? [];
  const primaryText =
    parsedText(parsed, "thesis", "summary") ??
    perspective?.thesis ??
    decision?.thesis ??
    "";
  const claimRebuttals =
    perspective?.claim_rebuttals?.length
      ? perspective.claim_rebuttals
      : parsedStringArray(parsed, "claim_rebuttals", "challenged_claims");
  const risks =
    perspective?.risks?.length
      ? perspective.risks
      : parsedStringArray(parsed, "risks", "downside_mechanisms");
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
          {artifact.generation_method ?? "legacy_unknown"}
        </small>
      </header>
      <div className="artifact-body">
        {diagnostics?.degraded_output && (
          <LegacyDiagnosticBanner diagnostics={diagnostics} />
        )}
        {parsed && (
          <p className="diagnostic-label">{t("parsedLegacyPayload")}</p>
        )}
        <Markdown
          evidenceAliases={evidenceIndex.aliases}
          onEvidence={onEvidence}
        >
          {primaryText}
        </Markdown>
        {parsed && (
          <details className="legacy-canonical-payload">
            <summary>{t("canonicalLegacyPayload")}</summary>
            <pre>
              {perspective?.thesis ?? decision?.thesis ?? ""}
            </pre>
          </details>
        )}
        {decision && (
          <p className="artifact-rating">
            <strong>{decision.rating}</strong> · {t("confidence")}{" "}
            {Math.round(decision.confidence * 100)}%
          </p>
        )}
        {perspective && (
          <>
            <List
              title={t("claimRebuttals")}
              items={claimRebuttals}
              emptyLabel={
                diagnostics?.missing_fields?.includes(
                  "claim_rebuttals",
                )
                  ? t("legacyFieldNotCaptured")
                  : undefined
              }
              evidenceIndex={evidenceIndex}
              onEvidence={onEvidence}
            />
            <List
              title={t("risks")}
              items={risks}
              emptyLabel={
                diagnostics?.missing_fields?.includes("risks")
                  ? t("legacyFieldNotCaptured")
                  : undefined
              }
              evidenceIndex={evidenceIndex}
              onEvidence={onEvidence}
            />
          </>
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
        {perspective && (perspective.new_evidence_refs ?? []).length > 0 && (
          <div className="artifact-new-evidence">
            <strong>{t("newEvidenceRefs")}</strong>
            <EvidenceRefs
              refs={perspective.new_evidence_refs ?? []}
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
  diagnostics,
  onEvidence,
  evidenceIndex,
}: {
  decision: ResearchDecision | null;
  diagnostics: ArtifactDiagnostics | null;
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
  const parsed = diagnostics?.parsed_thesis ?? null;
  const thesis = parsedText(parsed, "thesis") ?? decision.thesis;
  const catalysts =
    parsedStringArray(parsed, "catalysts").length > 0
      ? parsedStringArray(parsed, "catalysts")
      : (decision.catalysts ?? []);
  const risks =
    parsedStringArray(parsed, "risks").length > 0
      ? parsedStringArray(parsed, "risks")
      : (decision.risks ?? []);
  const invalidation =
    parsedStringArray(parsed, "invalidation_conditions").length > 0
      ? parsedStringArray(parsed, "invalidation_conditions")
      : (decision.invalidation_conditions ?? []);
  const parsedHorizon = parsedText(parsed, "time_horizon");
  const horizon =
    parsedHorizon ??
    (diagnostics?.sentinel_fields?.includes("time_horizon")
      ? t("legacyFieldNotCaptured")
      : decision.time_horizon);
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
        {diagnostics?.degraded_output && (
          <LegacyDiagnosticBanner diagnostics={diagnostics} />
        )}
        {diagnostics?.rating_conflict && (
          <div className="rating-conflict">
            <strong>{t("ratingConflict")}</strong>
            <span>
              {t("outerRating")}: {diagnostics.outer_rating} ·{" "}
              {t("nestedRating")}: {diagnostics.nested_rating}
            </span>
            <small>{t("unreliableConclusion")}</small>
          </div>
        )}
        <h2>{t("decision")}</h2>
        <h3>{t("thesis")}</h3>
        {parsed && (
          <p className="diagnostic-label">{t("parsedLegacyPayload")}</p>
        )}
        <div className="decision-thesis">
          <Markdown
            evidenceAliases={evidenceIndex.aliases}
            onEvidence={onEvidence}
          >
            {thesis}
          </Markdown>
        </div>
        {parsed && (
          <details className="legacy-canonical-payload">
            <summary>{t("canonicalLegacyPayload")}</summary>
            <pre>{decision.thesis}</pre>
          </details>
        )}
        <EvidenceRefs
          refs={decision.evidence_refs ?? []}
          onEvidence={onEvidence}
          evidenceIndex={evidenceIndex}
        />
        <MemoryRefs refs={decision.memory_refs ?? []} />
        <div className="decision-columns">
          <List
            title={t("catalysts")}
            items={catalysts}
            emptyLabel={t("noCatalystsIdentified")}
            evidenceIndex={evidenceIndex}
            onEvidence={onEvidence}
          />
          <List
            title={t("risks")}
            items={risks}
            emptyLabel={
              diagnostics?.sentinel_fields?.includes("risks")
                ? t("legacyFieldNotCaptured")
                : undefined
            }
            evidenceIndex={evidenceIndex}
            onEvidence={onEvidence}
          />
          <List
            title={t("invalidation")}
            items={invalidation}
            emptyLabel={
              diagnostics?.sentinel_fields?.includes(
                "invalidation_conditions",
              )
                ? t("legacyFieldNotCaptured")
                : undefined
            }
            evidenceIndex={evidenceIndex}
            onEvidence={onEvidence}
          />
        </div>
        <p className="horizon">
          <strong>{t("horizon")}:</strong> {horizon}
        </p>
      </div>
    </article>
  );
}

function LegacyDiagnosticBanner({
  diagnostics,
}: {
  diagnostics: ArtifactDiagnostics;
}) {
  const { t } = useTranslation();
  return (
    <div className="legacy-diagnostic" role="alert">
      <strong>
        {diagnostics.legacy_degraded_output
          ? t("legacyDegradedOutput")
          : t("degradedStructuredOutput")}
      </strong>
      <span>{t("legacyDegradedHint")}</span>
      {diagnostics.rerun_recommended && (
        <small>{t("rerunRecommended")}</small>
      )}
      {(diagnostics.reason_codes ?? []).length > 0 && (
        <code>{(diagnostics.reason_codes ?? []).join(", ")}</code>
      )}
    </div>
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
  onEvidence,
  evidenceIndex,
}: {
  report: unknown;
  onEvidence: (ref: string) => void;
  evidenceIndex: EvidenceReferenceIndex;
}) {
  const { t } = useTranslation();
  if (!report || typeof report !== "object") return null;
  const typed = report as {
    warnings?: Array<
      | string
      | {
          code: string;
          message: string;
          evidence_ref?: string | null;
          source?: string | null;
        }
    >;
    evidence_refs?: string[];
  };
  const warnings = typed.warnings ?? [];
  const refs = typed.evidence_refs ?? [];
  if (!warnings.length && !refs.length) return null;
  return (
    <div className="report-metadata">
      {warnings.length > 0 && (
        <div>
          <strong>{t("warnings")}</strong>
          <ul>
            {warnings.map((warning) => {
              const message =
                typeof warning === "string" ? warning : warning.message;
              const key =
                typeof warning === "string"
                  ? warning
                  : [
                      warning.code,
                      warning.evidence_ref,
                      warning.source,
                      warning.message,
                    ].join(":");
              return <li key={key}>{message}</li>;
            })}
          </ul>
        </div>
      )}
      <EvidenceRefs
        refs={refs}
        onEvidence={onEvidence}
        evidenceIndex={evidenceIndex}
      />
    </div>
  );
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

function isAnalystReport(content: ArtifactContent): content is AnalystReport {
  return "analyst" in content && "narrative" in content;
}

function isPerspectiveReview(
  content: ArtifactContent,
): content is PerspectiveReview {
  return "role" in content;
}

function isResearchDecision(
  content: ArtifactContent,
): content is ResearchDecision {
  return "rating" in content && "time_horizon" in content;
}

function parsedText(
  parsed: Record<string, unknown> | null | undefined,
  ...fields: string[]
): string | null {
  for (const field of fields) {
    const value = parsed?.[field];
    if (typeof value === "string" && value.trim()) {
      return value;
    }
  }
  return null;
}

function parsedStringArray(
  parsed: Record<string, unknown> | null | undefined,
  ...fields: string[]
): string[] {
  for (const field of fields) {
    const value = parsed?.[field];
    if (Array.isArray(value)) {
      return value
        .map(parsedArrayItem)
        .filter((item): item is string => Boolean(item));
    }
  }
  return [];
}

function parsedArrayItem(value: unknown): string | null {
  if (typeof value === "string") {
    return value.trim() || null;
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return null;
  }
  const record = value as Record<string, unknown>;
  const heading = parsedText(
    record,
    "mechanism",
    "claim_text",
    "title",
    "name",
  );
  const detail = parsedText(
    record,
    "detail",
    "challenge_text",
    "description",
    "text",
    "summary",
  );
  const refs = Array.isArray(record.evidence_refs)
    ? record.evidence_refs.filter(
        (item): item is string => typeof item === "string",
      )
    : [];
  const parts = [
    heading ? `**${heading}**` : null,
    detail,
    refs.length ? refs.join(", ") : null,
  ].filter((item): item is string => Boolean(item));
  return parts.length ? parts.join("\n\n") : null;
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
  const legacy = names
    .filter((name) => !(reportOrder as readonly string[]).includes(name))
    .sort();
  return [...known, ...legacy];
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

function formatTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}
