import { lazy, Suspense, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import { buildEvidenceReferenceIndex } from "../evidence";
import { researchLocation } from "../researchLinks";
import { Link } from "../router";
import { useReadingPosition } from "../useReadingPosition";
import type { RunRecord } from "../useRunRecord";
import { InstrumentIdentity } from "./Instruments";
import { ActionMenu } from "./Interaction";
import ResearchKindBadge from "./ResearchKindBadge";
import StatusBadge from "./StatusBadge";
import RunActivityView from "./RunActivityView";
import RunLifecycleDialog from "./RunLifecycleDialog";

const RunDiagnostics = lazy(() => import("./RunDiagnostics"));
const EvidenceSourceDrawer = lazy(() => import("./EvidenceSourceDrawer"));

export default function RunExecutionView({ record, diagnostics }: { record: RunRecord; diagnostics: boolean }) {
  const { t } = useTranslation();
  const { detail, artifacts, events, error, refresh, setError } = record;
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const [notice, setNotice] = useState("");
  const [restore, setRestore] = useState(false);
  const [source, setSource] = useState<string | null>(null);
  const index = useMemo(() => buildEvidenceReferenceIndex(record.evidence), [record.evidence]);
  useReadingPosition(`execution:${detail?.run.id}:${diagnostics}`);
  if (!detail) return null;
  const { run } = detail;
  const act = async (action: "cancel" | "retry") => {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError("");
    try { await api.action(run.id, action); setNotice(t(action === "cancel" ? "taskCancelSent" : "taskRetrySent")); await refresh(); }
    catch (cause) { setError(String(cause)); }
    finally { lock.current = false; setBusy(false); }
  };
  const partial = !!detail.result || artifacts.length > 0;
  return <section className="execution-page">
    <header className="page-header run-heading">
      <div><Link className="back-link" to="/runs">{t("runManagement")}</Link>
        <div className="run-title"><InstrumentIdentity ticker={run.request.ticker} instrumentName={run.instrument_name} instrumentLocalName={run.instrument_local_name} prominent /><ResearchKindBadge kind={run.research_kind} /><StatusBadge status={run.status} /></div>
        <p className="subtitle">{run.request.analysis_date}{run.full_baseline_run_id && <> · <Link to={`/timelines/${encodeURIComponent(run.request.ticker)}?node=${encodeURIComponent(run.full_baseline_run_id)}${run.trashed_at ? "&trash_state=all" : ""}`}>{t("fullBaseline")}</Link></>}</p>
      </div>
      <div className="action-row">
        {!run.trashed_at && ["running", "queued"].includes(run.status) && <button className="button danger" disabled={busy || run.cancel_requested} onClick={() => void act("cancel")}>{t(run.cancel_requested ? "taskCancelSent" : "cancel")}</button>}
        {!run.trashed_at && run.status === "failed" && <button className="button primary" disabled={busy} onClick={() => void act("retry")}>{t("retry")}</button>}
        {(run.is_research_node || partial) && <Link className="button primary" to={run.is_research_node ? researchLocation(run) : `/runs/${encodeURIComponent(run.id)}?view=${detail.result?.decision ? "decision" : "deliberation"}`}>{t(run.is_research_node ? "openResearch" : "openExistingResearch")}</Link>}
        {run.trashed_at && <button className="button" onClick={() => setRestore(true)}>{t("restore")}</button>}
        <ActionMenu label={t("moreResearchActions")}>
          <Link className="button" to={`/runs/${encodeURIComponent(run.id)}?view=${diagnostics ? "timeline" : "diagnostics"}`}>{t(diagnostics ? "activity" : "runDiagnostics")}</Link>
          {["succeeded", "failed", "cancelled"].includes(run.status) && <Link className="button" to={`/runs/new?intent=clone_full&from_run=${encodeURIComponent(run.id)}`}>{t("cloneAsFullResearch")}</Link>}
        </ActionMenu>
      </div>
    </header>
    {run.trashed_at && <p className="research-limitations">{t("trashedRun")}</p>}
    {notice && <p role="status" className="notice">{notice}</p>}
    {error && <p className="alert" role="alert">{error}<button className="button" onClick={() => void refresh()}>{t("retryLoad")}</button></p>}
    {run.error_message && <p className="alert">{run.error_message}</p>}
    <nav className="view-tabs" aria-label={t("executionDetails")}><Link id="run-tab-timeline" to={`/runs/${encodeURIComponent(run.id)}?view=timeline`} aria-current={!diagnostics ? "page" : undefined}>{t("activity")}</Link><Link id="run-tab-diagnostics" to={`/runs/${encodeURIComponent(run.id)}?view=diagnostics`} aria-current={diagnostics ? "page" : undefined}>{t("diagnostics")}</Link></nav>
    {diagnostics ? <Suspense fallback={<p role="status">{t("loading")}</p>}><RunDiagnostics detail={detail} events={events} artifacts={artifacts} evidence={record.evidence} evidenceIndex={index} onEvidence={setSource} /></Suspense> : <RunActivityView events={events} elapsedSeconds={run.metrics?.wall_time_seconds} researchKind={run.research_kind === "incremental" ? "incremental" : "full"} currentAttempt={run.attempt} runStatus={run.status} />}
    {restore && <RunLifecycleDialog action="restore" runIds={[run.id]} onClose={() => setRestore(false)} onDone={() => { setRestore(false); void refresh(); }} />}
    {source && <Suspense fallback={null}><EvidenceSourceDrawer evidenceRef={source} evidenceIndex={index} onClose={() => setSource(null)} /></Suspense>}
  </section>;
}
