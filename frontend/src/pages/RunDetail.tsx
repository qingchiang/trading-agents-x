import { NumericNoticeHandled, numericWarningLabel } from "../researchWarnings";
import { formatResearchDate } from "../researchDate";
import { createPortal } from "react-dom";
import { WorkspaceNavigationButtons } from "../components/ResearchWorkspace";
import EvidenceCoverage from "../components/EvidenceCoverage";
import PerformanceSection from "../components/PerformanceSection";
import { useReadingPosition } from "../useReadingPosition";
import type { TFunction } from "i18next";
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type AnalysisResult, type AnalystReport, type Capabilities, type EvidenceBundle, type IncrementalAnalysisBrief, type ResearchArtifact, type ResearchDecision, type ResearchNodeView, type RunDetail as RunDetailType, type RunEvent, } from "../api/client";
import EvidenceLinks from "../components/EvidenceLinks";
import EvidenceSourceDrawer from "../components/EvidenceSourceDrawer";
import { InstrumentIdentity } from "../components/Instruments";
import { ActionMenu, tabsKeyDown } from "../components/Interaction";
import ResearchKindBadge from "../components/ResearchKindBadge";
import RunActivityView from "../components/RunActivityView";
import StatusBadge from "../components/StatusBadge";
import { buildEvidenceReferenceIndex, type EvidenceDisplayGroup, type EvidenceReferenceIndex } from "../evidence";
import { baselineComponentText, groupReassessment, reassessmentDispositionCounts, type ReassessmentGroupKey, } from "../reassessment";
import { Link, useLocation, useNavigate, useParams, } from "../router";
import { formatUtcDate, trashDeadline } from "../trash";
import { useMarketDate } from "../useMarketDate";

const RunDiagnostics = lazy(() => import("../components/RunDiagnostics"));
const AnalystReportView = lazy(() => import("../components/AnalystReportView"));
const ResearchMarkdownReader = lazy(() =>
  import("../components/AnalystReportView").then((module) => ({
    default: module.ResearchMarkdownReader,
  })),
);
const DeliberationView = lazy(() => import("../components/DeliberationView"));
const EvidenceTableView = lazy(() => import("../components/EvidenceTableView"));
const Markdown = lazy(() => import("../components/Markdown"));
const ResearchDecisionView = lazy(() => import("../components/ResearchDecisionView"));
const ResearchDecisionContentView = lazy(() =>
  import("../components/ResearchDecisionView").then((module) => ({
    default: module.ResearchDecisionContent,
  })),
);

const terminal = new Set(["succeeded", "failed", "cancelled"]);
const reportOrder = ["fundamentals", "market", "news", "social"] as const;
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
  "node.numeric_audit_retry",
  "node.numeric_audit_recovered",
  "node.numeric_audit_degraded",
  "decision.numeric_display_scale_normalized",
  "decision.numeric_singleton_promoted",
  "decision.numeric_range_reordered",
  "artifact.created",
  "run.succeeded",
  "run.failed",
  "run.cancelled",
  "run.cancel_requested",
  "run.retry_queued",
  "incremental.collection_completed",
  "incremental.no_advancement",
  "incremental.synthesis_started",
  "incremental.synthesis_completed",
];
const viewNames = [
  "diagnostics",
  "incremental",
  "brief",
  "reassessment",
  "timeline",
  "deliberation",
  "evidence",
  "reports",
  "decision",
] as const;


type ViewName = (typeof viewNames)[number];
type ReturnViewName = Exclude<ViewName, "evidence">;
type ArtifactContent = ResearchArtifact["content"];
type VisibleWarning =
  | string
  | NonNullable<AnalystReport["warnings"]>[number];

export default function RunDetail({ selectedRunId, workspace = false, actionsTarget }: { selectedRunId?: string; workspace?: boolean; actionsTarget?: HTMLElement | null } = {}) {
  const { t } = useTranslation();
  const routerNavigate = useNavigate();
  const location = useLocation();
  const { runId: routeRunId = "" } = useParams();
  const runId = selectedRunId ?? routeRunId;
  const navigate = useCallback((to: string, options?: { replace?: boolean }) => {
    if (workspace && to.startsWith(`/runs/${encodeURIComponent(runId)}?`)) {
      const next = new URL(to, "http://local");
      const params = new URLSearchParams(location.search);
      for (const key of ["view", "report", "ref", "return_view", "return_report"]) params.delete(key);
      next.searchParams.forEach((value, key) => params.set(key, value));
      params.set("node", runId);
      routerNavigate(`${location.pathname}?${params}`, options);
    } else routerNavigate(to, options);
  }, [workspace, runId, location.pathname, location.search, routerNavigate]);
  const currentRun = useRef(runId);
  currentRun.current = runId;
  const [actionBusy, setActionBusy] = useState(false);
  const [loadedDetail, setDetail] = useState<RunDetailType | null>(null);
  const detail = loadedDetail?.run.id === runId ? loadedDetail : null;
  const [artifacts, setArtifacts] = useState<ResearchArtifact[]>([]);
  const [evidence, setEvidence] = useState<EvidenceBundle | null>(null);
  const [baselineEvidence, setBaselineEvidence] = useState<EvidenceBundle | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [error, setError] = useState("");
  const [sourceDrawerRef, setSourceDrawerRef] = useState<string | null>(null);
  const evidenceTrigger = useRef<{ element: HTMLElement; label: string | null; index: number; scroll: number } | null>(null);
  const [warningOpenRequest, setWarningOpenRequest] = useState(0);
  const [artifactRefreshRequest, setArtifactRefreshRequest] = useState(0);
  const latestEventSequence = useRef(0);
  const searchParams = useMemo(
    () => new URLSearchParams(location.search),
    [location.search],
  );
  const requestedView = searchParams.get("view");
  const isIncremental = detail?.run.research_kind === "incremental";
  const availableViews: ViewName[] = isIncremental
    ? ["brief", "reassessment", "decision", "evidence", "timeline", "diagnostics"]
    : ["decision", "reports", "deliberation", "evidence", "timeline", "diagnostics"];
  const defaultView: ViewName =
    detail?.run.status === "succeeded"
      ? isIncremental
        ? "brief"
        : "decision"
      : "timeline";
  const normalizedRequestedView =
    requestedView === "incremental" ? "brief" : requestedView;
  const activeView: ViewName =
    isViewName(normalizedRequestedView) && availableViews.includes(normalizedRequestedView)
      ? normalizedRequestedView
      : defaultView;
  useReadingPosition(`tradingagents-reading:${runId}:${activeView}`, undefined, Boolean(detail) && !["reports", "brief"].includes(activeView));
  const requestedReport = searchParams.get("report") ?? "";
  const focusedEvidence = searchParams.get("ref") ?? "";

  const refresh = useCallback(async () => {
    try {
      const nextDetail = await api.run(runId);
      if (currentRun.current !== runId) return;
      setDetail(nextDetail);
      setEvidence(nextDetail.result?.evidence ?? null);
      setError("");
    } catch (cause) {
      if (currentRun.current === runId) setError(cause instanceof Error ? cause.message : t("error"));
    }
  }, [runId, t]);

  useEffect(() => {
    if (!["deliberation", "diagnostics"].includes(activeView)) return;
    let active = true;
    void api.artifacts(runId).then(
      (items) => active && setArtifacts(items),
      (cause: unknown) =>
        active && setError(cause instanceof Error ? cause.message : t("error")),
    );
    return () => {
      active = false;
    };
  }, [activeView, artifactRefreshRequest, runId, t]);

  useEffect(() => {
    latestEventSequence.current = 0;
    setEvents([]);
    setDetail(null);
    setArtifacts([]);
    setEvidence(null);
    setSourceDrawerRef(null);
    void refresh();
  }, [refresh, runId]);

  useEffect(() => {
    if (!detail || detail.run.id !== runId || (terminal.has(detail.run.status) && activeView !== "timeline" && activeView !== "diagnostics" && activeView !== "deliberation")) return;
    const currentAttempt = detail.run.attempt;
    const source = new EventSource(
      `/api/v1/runs/${runId}/events?after=${latestEventSequence.current}`,
    );
    const receive = (raw: MessageEvent<string>) => {
      let event: RunEvent;
      try {
        event = JSON.parse(raw.data) as RunEvent;
      } catch {
        return;
      }
      latestEventSequence.current = Math.max(
        latestEventSequence.current,
        event.sequence,
      );
      setEvents((current) => {
        if (current.some((item) => item.sequence === event.sequence)) {
          return current;
        }
        return [...current, event];
      });
      if (
        event.event_type === "node.completed" ||
        event.event_type === "evidence.sealed" ||
        event.event_type.startsWith("incremental.") ||
        event.event_type.startsWith("run.")
      ) {
        void refresh();
      }
      if (event.event_type === "artifact.created") {
        setArtifactRefreshRequest((current) => current + 1);
      }
      if (
        event.attempt === currentAttempt &&
        (event.event_type === "run.succeeded" ||
          event.event_type === "run.failed" ||
          event.event_type === "run.cancelled")
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
  }, [detail?.run.attempt, detail?.run.id, detail?.run.status, activeView, refresh, runId]);

  useEffect(() => {
    if (!detail?.run.trashed_at) return;
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
  }, [detail?.run.trashed_at]);

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
  const currentEvidenceIndex = useMemo(
    () => buildEvidenceReferenceIndex(evidence),
    [evidence],
  );
  const evidenceIndex = useMemo(
    () => buildEvidenceReferenceIndex(evidence, baselineEvidence),
    [baselineEvidence, evidence],
  );
  const decision = useMemo(
    () =>
      detail?.result?.decision ??
      latestResearchDecision(artifacts),
    [artifacts, detail?.result?.decision],
  );
  const runWarnings = useMemo(() => {
    const reportWarningKeys = new Set([...Object.values(reports).flatMap(reportWarnings), ...(detail?.incremental_context?.analysis_brief?.warnings ?? [])].map(warningKey));
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
      currentEvidenceIndex.primaryRefs[focusedEvidence] ?? focusedEvidence;
    const target = document.getElementById(`evidence-${targetRef}`);
    target?.focus();
    target?.scrollIntoView?.({ behavior: "smooth", block: "center" });
  }, [
    activeView,
    evidence?.digest,
    evidence?.items.length,
    currentEvidenceIndex.primaryRefs,
    focusedEvidence,
  ]);

  useEffect(() => {
    setBaselineEvidence(null);
  }, [runId]);

  useEffect(() => {
    const baselineRunId = detail?.incremental_context?.full_baseline.run_id;
    if (
      !baselineRunId ||
      baselineEvidence ||
      !["decision", "brief", "reassessment"].includes(activeView)
    ) return;
    let active = true;
    void api.evidence(baselineRunId).then(
      (bundle) => active && setBaselineEvidence(bundle),
      () => active && setBaselineEvidence(null),
    );
    return () => {
      active = false;
    };
  }, [
    activeView,
    baselineEvidence,
    detail?.incremental_context?.full_baseline.run_id,
  ]);

  const selectView = useCallback(
    (view: ViewName) => {
      navigate(
        runDetailPath(runId, {
          view,
          report: view === "reports" && activeReport ? activeReport : undefined,
          return_view: view === "evidence" && activeView !== "evidence" ? activeView : undefined,
          return_report: view === "evidence" && activeView === "reports" ? activeReport : undefined,
        }),
      );
    },
    [activeReport, activeView, navigate, runId],
  );

  const selectReport = useCallback(
    (report: string) => {
      navigate(
        runDetailPath(runId, {
          view: "reports",
          report,
        }),
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
    const element = document.activeElement as HTMLElement;
    const label = element.getAttribute("aria-label");
    const peers = Array.from(document.querySelectorAll<HTMLElement>("button[aria-label]")).filter(item => item.getAttribute("aria-label") === label);
    evidenceTrigger.current = { element, label, index: peers.indexOf(element), scroll: window.scrollY };
    setSourceDrawerRef(ref);
  }, []);
  const closeSourceDrawer = useCallback(() => {
    setSourceDrawerRef(null);
    requestAnimationFrame(() => {
      const trigger = evidenceTrigger.current;
      if (!trigger) return;
      const replacement = Array.from(document.querySelectorAll<HTMLElement>("button[aria-label]")).filter(item => item.getAttribute("aria-label") === trigger.label)[Math.max(0, trigger.index)];
      window.scrollTo?.(0, trigger.scroll);
      (trigger.element.isConnected ? trigger.element : replacement)?.focus({ preventScroll: true });
    });
  }, []);

  const requestedReturnView = searchParams.get("return_view");
  const returnView: ReturnViewName = isReturnViewName(requestedReturnView)
    ? requestedReturnView
    : isIncremental ? "brief" : "decision";
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
    if (actionBusy) return;
    setActionBusy(true);
    try {
      await api.action(runId, action);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("error"));
    } finally { setActionBusy(false); }
  };

  const restore = async () => {
    try {
      await api.restoreRuns([runId]);
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("error"));
    }
  };

  const marketDate = useMarketDate(detail?.run.request.ticker);

  if (!detail || detail.run.id !== runId) {
    return <div className="loading">{error || t("loading")}</div>;
  }
  const { run } = detail;
  const canUpdateResearch =
    run.is_research_node &&
    run.status === "succeeded" &&
    !run.trashed_at &&
    marketDate.date !== null && run.request.analysis_date < marketDate.date;
  const hasPartialResearch =
    run.status !== "succeeded" &&
    (evidence !== null ||
      artifacts.length > 0 ||
      Object.keys(reports).length > 0 ||
      decision !== null);

  const readingViews = availableViews.filter(view => !["timeline", "diagnostics"].includes(view) || !workspace);
  const actions = (
        <div className="action-row">
          {!run.trashed_at &&
            (run.status === "queued" || run.status === "running") && (
            <button className="button danger" disabled={actionBusy || run.cancel_requested} onClick={() => void act("cancel")}>
              {t("cancel")}
            </button>
          )}
          {!run.trashed_at && run.status === "failed" && (
            <button className="button" disabled={actionBusy} onClick={() => void act("retry")}>
              {t("retry")}
            </button>
          )}
          {run.trashed_at && !run.is_research_node && (
            <button className="button primary" onClick={() => void restore()}>
              {t("restore")}
            </button>
          )}
          {!workspace && run.is_research_node && (
            <Link
              className="button"
              to={`/timelines/${encodeURIComponent(run.request.ticker)}`}
            >
              {t("researchTimeline")}
            </Link>
          )}
          {marketDate.error && <span className="warning" role="status">{t("futureContextUnavailable")} <button className="button" onClick={marketDate.retry}>{t("retryLoad")}</button></span>}
          {canUpdateResearch && (
            <Link
              className="button primary"
              to={`/runs/new?intent=update&from_run=${encodeURIComponent(runId)}&full_baseline_run_id=${encodeURIComponent(run.research_kind === "incremental" ? run.full_baseline_run_id ?? "" : run.id)}`}
            >
              {t("updateThisResearch")}
            </Link>
          )}
          {run.is_research_node &&
            run.status === "succeeded" &&
            !run.trashed_at &&
            !canUpdateResearch && (
              <span className="button disabled" title={t(marketDate.date ? "sameDayUpdateUnavailable" : "loading")}>
                {t("updateThisResearch")}
              </span>
            )}
          {!workspace && terminal.has(run.status) && (
            <Link
              className="button"
              to={`/runs/new?intent=clone_full&from_run=${encodeURIComponent(runId)}`}
            >
              {t("cloneAsFullResearch")}
            </Link>
          )}
          <ActionMenu label={t("exportResearch")}>
          <a
            className="button"
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
          </ActionMenu>
          {!workspace && activeView === "diagnostics" && <Link className="button" to={`/runs/${encodeURIComponent(runId)}?view=timeline`}>{t("activity")}</Link>}
          {workspace ? !actionsTarget && <ActionMenu label={t("moreResearchActions")}>
            {terminal.has(run.status) && <Link className="button" to={`/runs/new?intent=clone_full&from_run=${encodeURIComponent(runId)}`}>{t("cloneAsFullResearch")}</Link>}
            <Link className="button" to={`/runs/${encodeURIComponent(runId)}?view=diagnostics`}>{t("runDiagnostics")}</Link>
          </ActionMenu> : <Link className="text-link" to={`/runs/${encodeURIComponent(runId)}?view=diagnostics`}>{t("runDiagnostics")}</Link>}
        </div>
  );

  return (
    <section className={workspace ? "workspace-reader" : "run-reader"}>
      <header className={`page-header run-heading ${actionsTarget ? "actions-relocated" : ""}`}>
        <div>
          {!workspace && <Link className="back-link" to="/">
            ← {t("dashboard")}
          </Link>}
          {!workspace && <div className="run-title">
            <InstrumentIdentity
              ticker={run.request.ticker}
              instrumentName={run.instrument_name}
              instrumentLocalName={run.instrument_local_name}
              prominent
            />
            <ResearchKindBadge
              kind={run.research_kind}
              request={run.request}
              methodSnapshot={run.method_snapshot}
            />
            <StatusBadge status={run.status} />
          </div>}
          <p className="subtitle">
            {run.request.analysis_date}
            {(activeView === "timeline" || activeView === "diagnostics") && " · " + t("attempt") + " " + run.attempt}
          </p>
          {run.source_run_id && (
            <p className="subtitle">
              {t("sourceRun")}:{" "}
              <Link to={`/runs/${encodeURIComponent(run.source_run_id)}`}>
                {t("sourceRun")}
              </Link>
            </p>
          )}
        </div>
        {actionsTarget ? createPortal(actions, actionsTarget) : actions}
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
      <RunWarnings
        warnings={runWarnings}
        openRequest={warningOpenRequest}
        key={runId}
      />

      <div className="reading-toolbar">
        <select className="mobile-reading-view" aria-label={t("researchViews")} value={activeView} onChange={event => selectView(event.target.value as ViewName)}>
          {readingViews.map(view => <option value={view} key={view}>{t(viewLabel(view, isIncremental))}</option>)}
        </select>
      <nav
        className="panel view-tabs"
        aria-label={t("researchViews")}
        role="tablist"
        onKeyDown={tabsKeyDown}
      >
        {readingViews.map((view) => (
          <button
            type="button"
            role="tab"
            aria-selected={activeView === view}
            tabIndex={activeView === view ? 0 : -1}
            id={`run-tab-${view}`}
            aria-controls={`run-view-${view}`}
            className={activeView === view ? "active" : ""}
            onClick={() => selectView(view)}
            key={view}
          >
            {t(viewLabel(view, isIncremental))}
          </button>
        ))}
      </nav>
        <WorkspaceNavigationButtons />
      </div>

      <NumericNoticeHandled.Provider value={runWarnings.some(warning => numericWarningLabel(warning) !== null)}><Suspense fallback={<div className="loading" role="status">{t("loading")}</div>}>
        {activeView === "decision" && isIncremental && detail.research_node && (
          <IncrementalDecisionPanel
            decision={decision}
            node={detail.research_node}
            numericAudit={detail.result?.numeric_audit}
            evidenceIndex={evidenceIndex}
            onEvidence={openSourceDrawer}
            onOpenWarnings={() => setWarningOpenRequest((value) => value + 1)}
          />
        )}

        {activeView === "brief" && isIncremental && (
          <>
          <IncrementalBriefPanel
            brief={detail.incremental_context?.analysis_brief ?? null}
            baselineDate={detail.incremental_context?.full_baseline.analysis_date}
            onView={selectView}
            node={detail.research_node ?? null}
            runId={run.id}
            runStatus={run.status}
            evidenceIndex={evidenceIndex}
            onEvidence={openSourceDrawer}
          />

          </>
        )}

        {activeView === "reassessment" && detail.research_node && (
          <ReassessmentPanel
            node={detail.research_node}
            baselineDecision={detail.incremental_context?.full_baseline.decision ?? null}
            currentDecision={decision}
            evidenceIndex={evidenceIndex}
            onEvidence={openSourceDrawer}
          />
        )}

        {activeView === "timeline" && (
          <RunActivityView
            elapsedSeconds={run.metrics?.wall_time_seconds}
            events={events}
            researchKind={isIncremental ? "incremental" : "full"}
            currentAttempt={run.attempt}
            runStatus={run.status}
          />
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
            evidenceIndex={currentEvidenceIndex}
            onEvidence={openEvidence}
            onSourceDetails={openSourceDrawer}
            incremental={isIncremental}
            incrementalNode={isIncremental ? detail.research_node ?? null : null}
          />
        )}
        {activeView === "reports" && (
          <ReportsPanel
            runId={run.id}
            reports={reports}
            reportNames={reportNames}
            activeReport={activeReport}
            onReport={selectReport}
            onEvidence={openSourceDrawer}
            evidenceIndex={evidenceIndex}
          />
        )}
        {activeView === "decision" && !isIncremental && (
          <DecisionPanel
            decision={decision}
            numericAudit={detail.result?.numeric_audit}
            onEvidence={openSourceDrawer}
            evidenceIndex={evidenceIndex}
            onOpenWarnings={() => setWarningOpenRequest((value) => value + 1)}
          />
        )}
      </Suspense></NumericNoticeHandled.Provider>

      {activeView === "diagnostics" && <Suspense fallback={<div role="status">{t("loading")}</div>}>
        <RunDiagnostics detail={detail} events={events} artifacts={artifacts} evidenceIndex={evidenceIndex} onEvidence={openSourceDrawer} />
      </Suspense>}

      <Suspense fallback={<div className="loading" role="status">{t("loading")}</div>}>
        <EvidenceSourceDrawer
          evidenceRef={sourceDrawerRef}
          evidenceIndex={evidenceIndex}
          onClose={closeSourceDrawer}
          key={sourceDrawerRef ?? "closed"}
        />
      </Suspense>
    </section>
  );
}

function IncrementalDecisionPanel({
  decision,
  node,
  numericAudit,
  evidenceIndex,
  onEvidence,
  onOpenWarnings,
}: {
  decision: ResearchDecision | null;
  node: ResearchNodeView;
  numericAudit: AnalysisResult["numeric_audit"];
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
  onOpenWarnings: () => void;
}) {
  const { t } = useTranslation();
  if (!decision) {
    return (
      <article className="panel audit-panel">
        <div className="empty-state">{t("noDecision")}</div>
      </article>
    );
  }
  return (
    <article
      className="panel audit-panel decision-panel-v2 incremental-decision-panel"
      id="run-view-decision" aria-labelledby="run-tab-decision"
      role="tabpanel"
    >
      <IncrementalOutcomeSummary node={node} />
      <ResearchDecisionContentView
        decision={decision}
        numericAudit={numericAudit}
        evidenceIndex={evidenceIndex}
        onEvidence={onEvidence}
        onOpenWarnings={onOpenWarnings}
      />
    </article>
  );
}

function IncrementalBriefPanel({
  onView,
  baselineDate,
  brief,
  node,
  runId,
  runStatus,
  evidenceIndex,
  onEvidence,
}: {
  onView: (view: ViewName) => void;
  baselineDate?: string;
  brief: IncrementalAnalysisBrief | null;
  node: ResearchNodeView | null;
  runId: string;
  runStatus: RunDetailType["run"]["status"];
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <article
      className="panel audit-panel report-panel reader-panel"
      id="run-view-brief" aria-labelledby="run-tab-brief"
      role="tabpanel"
    >
      <div className="panel-header">
        <div>
          <p className="eyebrow">{t("incrementalResearch")}</p>
          <h2>{t("analysisBrief")}</h2>
        </div>
      </div>
      {node && <><p className="brief-period">{t("baselineDate")}: {baselineDate ?? "—"} → {t("selectedCutoff")}: {node.analysis_date}</p><IncrementalOutcomeSummary node={node} /></>}
      {!brief ? (
        <div className="empty-state">
          {t(briefUnavailableLabel(runStatus))}
          <p><button className="button" onClick={() => onView("reassessment")}>{t("reassessment")}</button> <button className="button" onClick={() => onView("decision")}>{t("completeJudgment")}</button></p>
        </div>
      ) : (
        <ResearchMarkdownReader
          before={<ResearchLimitations warnings={brief.warnings ?? []} sections={brief.report_sections} />}
          markdown={brief.markdown}
          sections={brief.report_sections}
          runId={runId}
          reportKey="incremental-brief"
          extraSections={node?.performance ? [{ id: "workspace-period-performance", anchor: "workspace-period-performance", title: t("performance"), source_refs: [] }] : []}
          after={node && <PerformanceSection node={node} baselineDate={baselineDate} />}
          evidenceIndex={evidenceIndex}
          onEvidence={onEvidence}

        />
      )}
      {!brief && node && <PerformanceSection node={node} baselineDate={baselineDate} />}
    </article>
  );
}

function IncrementalOutcomeSummary({ node }: { node: ResearchNodeView }) {
  const { t } = useTranslation();
  const currentNodeReasons = node.full_research_required_reasons ?? [];
  const currentNodeTriggeredWarning = currentNodeReasons.length > 0;
  const showCycleWarning = currentNodeTriggeredWarning || node.cycle_warning;
  return (
    <section className="incremental-outcome-summary" aria-label={t("decisionOutcome")}>
      <div>
        <p className="eyebrow">{t("decisionOutcome")}</p>
        <strong>
          {node.decision_outcome
            ? t(`decisionOutcome_${node.decision_outcome}`)
            : t("decisionOutcomeNotRecorded")}
        </strong>
        {node.decision_outcome_reason && <p>{node.decision_outcome_reason}</p>}
      </div>
      {showCycleWarning && (
        <section className="research-warning-block" role="status">
          <h2>{t("fullResearchRecommended")}</h2>
          <p>
            {t(
              currentNodeTriggeredWarning
                ? "fullResearchTriggeredByCurrentNode"
                : "fullResearchTriggeredByEarlierNode",
            )}
          </p>
          {currentNodeReasons.map((reason) => (
            <p key={reason.code}>{reason.message}</p>
          ))}
        </section>
      )}
    </section>
  );
}

function ReassessmentPanel({
  node,
  baselineDecision,
  currentDecision,
  evidenceIndex,
  onEvidence,
}: {
  node: ResearchNodeView;
  baselineDecision: ResearchDecision | null;
  currentDecision: ResearchDecision | null;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
}) {
  const { t } = useTranslation();
  const entries = node.reassessment?.entries ?? [];
  const counts = reassessmentDispositionCounts(entries);
  const groups =
    baselineDecision && currentDecision
      ? groupReassessment(entries)
      : [];
  return (
    <article
      className="panel audit-panel incremental-reassessment-panel"
      id="run-view-reassessment" aria-labelledby="run-tab-reassessment"
      role="tabpanel"
    >
      <div className="panel-header">
        <div>
          <p className="eyebrow">{t("incrementalResearch")}</p>
          <h2>{t("reassessment")}</h2>
        </div>
      </div>
      {entries.length > 0 && <div className="reassessment-counts" aria-label={t("reassessmentSummary")}>
        {[
          "strengthened",
          "weakened",
          "overturned",
          "unresolved",
          "reaffirmed",
        ].map((disposition) => (
          <span
            className={`reassessment-count ${disposition}`}
            key={disposition}
          >
            {t(`reassessment_${disposition}`)}{" "}
            <strong>{counts[disposition] ?? 0}</strong>
          </span>
        ))}
      </div>}
      {!baselineDecision || !currentDecision ? (
        <div className="empty-state">{t("baselineDecisionUnavailable")}</div>
      ) : groups.length === 0 ? (
        <div className="empty-state">{t("notRecorded")}</div>
      ) : (
        <div className="reassessment-groups">
          {[...groups].sort((a, b) => Number(b.entries.some(entry => entry.disposition !== "reaffirmed")) - Number(a.entries.some(entry => entry.disposition !== "reaffirmed"))).map((group) => {
            const changed = group.entries.filter(
              (entry) => entry.disposition !== "reaffirmed",
            );
            const reaffirmed = group.entries.filter(
              (entry) => entry.disposition === "reaffirmed",
            );
            return (
              <details
                className="reassessment-group"
                id={`reassessment-${group.key}`} data-outline={t(reassessmentGroupLabel(group.key))}
                open={changed.length > 0}
                key={group.key}
              >
                <summary>
                  <span>{t(reassessmentGroupLabel(group.key))}</span>
                  <small>
                    {t("changedReassessmentCount", {
                      changed: changed.length,
                      total: group.entries.length,
                    })}
                  </small>
                </summary>
                <div className="reassessment-entry-list">
                  {changed.map((entry) => (
                    <ReassessmentEntryCard
                      entry={entry}
                      baselineDecision={baselineDecision}
                      evidenceIndex={evidenceIndex}
                      onEvidence={onEvidence}
                      key={entry.component_id}
                    />
                  ))}
                  {reaffirmed.length > 0 && (
                    <details className="reaffirmed-list">
                      <summary>
                        {t("showReaffirmed", { count: reaffirmed.length })}
                      </summary>
                      {reaffirmed.map((entry) => (
                        <ReassessmentEntryCard
                          entry={entry}
                          baselineDecision={baselineDecision}
                          evidenceIndex={evidenceIndex}
                          onEvidence={onEvidence}
                          key={entry.component_id}
                        />
                      ))}
                    </details>
                  )}
                </div>
              </details>
            );
          })}
        </div>
      )}

    </article>
  );
}

function ReassessmentEntryCard({
  entry,
  baselineDecision,
  evidenceIndex,
  onEvidence,
}: {
  entry: NonNullable<ResearchNodeView["reassessment"]>["entries"][number];
  baselineDecision: ResearchDecision;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
}) {
  const { t } = useTranslation();
  const baselineText = baselineComponentText(baselineDecision, entry.component_id);
  return (
    <article className={`reassessment-entry ${entry.disposition}`}>
      <span className="reassessment-disposition">
        {t(`reassessment_${entry.disposition}`)}
      </span>
      <div>
        <h4>{t("baselineContent")}</h4>
        <Markdown evidenceAliases={evidenceIndex.aliases} onEvidence={onEvidence}>{baselineText ?? t("notRecorded")}</Markdown>
      </div>
      <div>
        <h4>{t("reassessmentReason")}</h4>
        <Markdown evidenceAliases={evidenceIndex.aliases} onEvidence={onEvidence}>{entry.reason}</Markdown>
      </div>
      <EvidenceLinks
        refs={entry.evidence_refs ?? []}
        evidenceIndex={evidenceIndex}
        onEvidence={onEvidence}
        compact
      />
    </article>
  );
}

function advancementLabel(t: TFunction, reasons: string[]): string {
  if (reasons.length === 0) return t("notRecorded");
  const labels: Record<string, string> = {
    admissible_observation: "advancementAdmissibleObservation",
    completed_stock_session: "advancementCompletedMarketSession",
    newly_completed_market_session: "advancementCompletedMarketSession",
    near_live_advisory: "advancementNearLiveAdvisory",
  };
  return reasons
    .map((reason) => t(labels[reason] ?? "advancementOther", { reason }))
    .join(", ");
}

function reassessmentGroupLabel(group: ReassessmentGroupKey): string {
  return `reassessmentGroup_${group}`;
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
      className="panel audit-panel reader-panel"
      id="run-view-deliberation" aria-labelledby="run-tab-deliberation"
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
  onSourceDetails,
  incremental,
  incrementalNode,
}: {
  evidence: EvidenceBundle | null;
  evidenceStatus: RunDetailType["evidence_status"]["status"];
  runStatus: RunDetailType["run"]["status"];
  focusedRef: string;
  onReturn: () => void;
  returnLabel: string;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
  onSourceDetails: (ref: string) => void;
  incremental: boolean;
  incrementalNode: ResearchNodeView | null;
}) {
  const { t } = useTranslation();
  const location = useLocation();
  const [domainFilter, setDomainFilter] = useState<{ domain: string; refs: string[] } | null>(null);
  const [query, setQuery] = useState("");
  const [source, setSource] = useState("");
  const sources = [...new Set(evidenceIndex.groups.flatMap(group => group.sources))].sort();
  const groups = evidenceIndex.groups.filter(group => (!domainFilter || group.refs.some(ref => domainFilter.refs.includes(ref))) && (!source || group.sources.includes(source)) &&
    [group.canonical.content, group.canonical.value, ...group.sources, ...group.items.map(item => item.effective_date)].join(" ").toLocaleLowerCase().includes(query.trim().toLocaleLowerCase()));
  return (
    <article
      className="panel audit-panel"
      id="run-view-evidence" aria-labelledby="run-tab-evidence"
      role="tabpanel"
    >
      <div className="panel-header">
        <div>
          <p className="eyebrow">{t("evidenceBundle")}</p>
          <h2>{t(incremental ? "evidenceUpdates" : "evidence")}</h2>
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
      {location.returnEvidence && <p className="evidence-domain-filter"><Link to={location.returnEvidence.url}>{t("returnToUpdateEvidence")}</Link></p>}
      {incremental && incrementalNode && <EvidenceCoverage node={incrementalNode} evidenceIndex={evidenceIndex} onDomain={(domain, refs) => {
        setDomainFilter({ domain, refs }); setQuery(""); setSource("");
        requestAnimationFrame(() => { const target = document.getElementById("evidence-list"); target?.scrollIntoView?.({ block: "start" }); target?.focus({ preventScroll: true }); });
      }} />}
      {!evidence ? (
        <div className="empty-state">
          {evidenceStatus === "pending" &&
          (runStatus === "queued" || runStatus === "running")
            ? t("evidencePending")
            : t("noEvidenceRecorded")}
        </div>
      ) : (
        <>
          {incremental ? (
            <>
              <EvidenceBundleSummary
                evidence={evidence}
                evidenceIndex={evidenceIndex}
                mode="readable"
              />
            </>
          ) : (
            <EvidenceBundleSummary
              evidence={evidence}
              evidenceIndex={evidenceIndex}
            />
          )}
          <div className="evidence-filters" tabIndex={-1} id="evidence-list" data-outline={t("evidence")}>
            <label>{t("searchEvidence")}<input type="search" value={query} onChange={event => setQuery(event.target.value)} /></label>
            <label>{t("evidenceSourceFilter")}<select value={source} onChange={event => setSource(event.target.value)}><option value="">{t("all")}</option>{sources.map(value => <option key={value}>{value}</option>)}</select></label>
          </div>
          {domainFilter && <p className="evidence-domain-filter">{t(`${domainFilter.domain}Analyst`)} <button type="button" className="text-button" onClick={() => setDomainFilter(null)}>{t("clearDomainFilter")}</button></p>}
          {!groups.length && <p role="status">{t("noEvidenceMatches")}</p>}
          <div className="evidence-list">
            {groups.map((group) => (
              <EvidenceCard
                group={group}
                focused={group.refs.includes(focusedRef)}
                onSourceDetails={onSourceDetails}
                key={group.alias}
              />
            ))}
          </div>
          {(evidence.tables ?? []).length > 0 && (
            <section className="evidence-table-list">
              <h3 id="evidence-tables" data-outline={t("rawEvidenceTables")}>{t("rawEvidenceTables")}</h3>
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

function EvidenceBundleSummary({
  evidence,
  evidenceIndex,
  mode = "readable",
}: {
  evidence: EvidenceBundle;
  evidenceIndex: EvidenceReferenceIndex;
  mode?: "all" | "readable" | "technical";
}) {
  const { t, i18n } = useTranslation();
  return (
    <dl className="bundle-summary">
      {mode !== "readable" && (
        <>
          <div>
            <dt>{t("evidenceDigest")}</dt>
            <dd>{evidence.digest ?? "—"}</dd>
          </div>
          <div>
            <dt>{t("version")}</dt>
            <dd>{evidence.version ?? "1"}</dd>
          </div>
        </>
      )}
      {mode !== "technical" && (
        <>
          <div>
            <dt>{t("analysisDate")}</dt>
            <dd>{formatResearchDate(evidence.analysis_date, i18n.language)}</dd>
          </div>
          <div>
            <dt>{t("displayedEvidence")}</dt>
            <dd>
              {t("evidenceCount", { count: evidenceIndex.groups.length })}
            </dd>
          </div>
        </>
      )}
    </dl>
  );
}

function EvidenceCard({
  group,
  focused,
  onSourceDetails,
}: {
  group: EvidenceDisplayGroup;
  focused: boolean;
  onSourceDetails: (ref: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const item = group.canonical;
  const hasValue = item.value !== null && item.value !== undefined;
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
  const metadata = (
    <dl className="evidence-metadata">
      <div>
        <dt>{t("source")}</dt>
        <dd>{group.sources.join(", ")}</dd>
      </div>
      <div>
        <dt>{t("effectiveDate")}</dt>
        <dd>{effectiveDates.map(date => formatResearchDate(date, i18n.language)).join(", ") || "—"}</dd>
      </div>
      <div>
        <dt>{t("availableAt")}</dt>
        <dd>{availableDates.map(date => formatResearchDate(date, i18n.language)).join(", ") || "—"}</dd>
      </div>
      <div>
        <dt>{t("fallback")}</dt>
        <dd>{group.fallback ? t("yes") : t("no")}</dd>
      </div>
      <div><dt>{t("evidenceOrigin")}</dt><dd>{group.origins.map(origin => t(`evidenceOrigin_${origin}`)).join(", ")}</dd></div>
      {(item.origins ?? []).map((origin, n) => <div key={n}><dt>{origin.source}</dt><dd>{t(`temporal_${origin.temporal_scope ?? "unknown"}`)}</dd></div>)}
      {hasValue && (
        <div>
          <dt>{t("value")}</dt>
          <dd>
            {String(item.value)} {item.unit ?? ""}
          </dd>
        </div>
      )}
    </dl>
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
          <span className="evidence-alias">{group.alias}</span>
          <h3>{group.sources.join(" · ")}</h3>
        </div>
        <span className={`quality quality-${group.quality}`}>
          {t(`quality_${group.quality}`)}
        </span>
      </header>
      {metadata}
      {item.content && (
        <div className="evidence-content">
          <Markdown>{item.content}</Markdown>
        </div>
      )}
      <button
        type="button"
        className="button compact-button evidence-source-button"
        onClick={() => onSourceDetails(item.ref)}
      >
        {t("viewSourceDetails")}
      </button>
    </section>
  );
}

function ReportsPanel({
  runId,
  reports,
  reportNames,
  activeReport,
  onReport,
  onEvidence,
  evidenceIndex,
}: {
  runId: string;
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
      className="panel audit-panel report-panel reader-panel"
      id="run-view-reports" aria-labelledby="run-tab-reports"
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
          <div className="tabs" role="tablist" aria-label={t("reports")} onKeyDown={tabsKeyDown}>
            {reportNames.map((name) => (
              <button
                role="tab" aria-selected={activeReport === name} tabIndex={activeReport === name ? 0 : -1}
                className={activeReport === name ? "active" : ""}
                onClick={() => onReport(name)}
                key={name}
              >
                {reportLabel(t, name)}
              </button>
            ))}
          </div>
          <ResearchLimitations warnings={reportWarnings(reports[activeReport])} sections={typeof reports[activeReport] === "string" ? [] : (reports[activeReport] as AnalystReport).report_sections} />
          <AnalystReportView
            report={reports[activeReport]}
            runId={runId}
            reportKey={activeReport}
            evidenceIndex={evidenceIndex}
            onEvidence={onEvidence}
          />
        </>
      )}
    </article>
  );
}

function DecisionPanel({
  decision,
  numericAudit,
  onEvidence,
  evidenceIndex,
  onOpenWarnings,
}: {
  decision: ResearchDecision | null;
  numericAudit: AnalysisResult["numeric_audit"];
  onEvidence: (ref: string) => void;
  evidenceIndex: EvidenceReferenceIndex;
  onOpenWarnings: () => void;
}) {
  return (
    <ResearchDecisionView
      decision={decision}
      numericAudit={numericAudit}
      onEvidence={onEvidence}
      evidenceIndex={evidenceIndex}
      onOpenWarnings={onOpenWarnings}
    />
  );
}

function ResearchLimitations({ warnings, sections = [] }: { warnings: VisibleWarning[]; sections?: AnalystReport["report_sections"] }) {
  const { t } = useTranslation();
  const items = dedupeWarnings(warnings);
  if (!items.length) return null;
  return <section className="research-limitations" role="status">
    <strong>{t("researchLimitations")}</strong>
    <ul>{items.map(warning => {
      const reference = typeof warning === "string" ? undefined : warning.evidence_ref;
      const related = reference ? sections.filter(section => section.source_refs?.includes(reference)) : [];
      return <li key={warningKey(warning)}>{numericWarningLabel(warning) ? t(numericWarningLabel(warning)!) : warningMessage(warning)}
        {related.map(section => <a className="text-link limitation-section-link" key={section.id} href={`#${section.anchor}`} onClick={event => {
          event.preventDefault();
          const target = document.getElementById(`user-content-${section.anchor}`);
          target?.scrollIntoView({ block: "start" });
          target?.focus({ preventScroll: true });
          window.history.replaceState(window.history.state, "", `#${section.anchor}`);
        }}>{section.title}</a>)}
      </li>;
    })}</ul>
  </section>;
}

function RunWarnings({ warnings, openRequest }: { warnings: VisibleWarning[]; openRequest: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { if (openRequest) ref.current?.scrollIntoView?.({ block: "center" }); }, [openRequest]);
  return <div ref={ref} id="run-warnings"><ResearchLimitations warnings={warnings} /></div>;
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
    diagnostics: "diagnostics",
    incremental: "returnToIncrementalSummary",
    brief: "returnToAnalysisBrief",
    reassessment: "returnToReassessment",
    timeline: "returnToActivity",
    deliberation: "returnToDeliberation",
    reports: "returnToReports",
    decision: "returnToOverview",
  };
  return t(labels[view]);
}

function viewLabel(view: ViewName, incremental: boolean): string {
  if (view === "decision") return incremental ? "completeJudgment" : "overview";
  if (view === "brief") return "analysisBrief";
  if (view === "timeline") return "activity";
  if (view === "evidence" && incremental) return "evidenceUpdates";
  return view;
}

function briefUnavailableLabel(
  status: RunDetailType["run"]["status"],
): string {
  if (status === "queued" || status === "running") {
    return "analysisBriefPending";
  }
  if (status === "failed" || status === "cancelled") {
    return "analysisBriefNotProduced";
  }
  return "historicalBriefUnavailable";
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
