import { lazy, Suspense, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { legacyResearchLocation, readingViews } from "../research/researchLinks";
import { useLocation, useNavigate, useParams } from "../../app/router";
import { useRunRecord } from "../../shared/useRunRecord";
import RunExecutionView from "./RunExecutionView";

const LegacyReader = lazy(() => import("../research/ResearchReader").then(module => ({ default: module.ResearchReaderContent })));

/** Preserve old reading links while keeping the default route focused on execution. */
export default function RunDetail() {
  const { t } = useTranslation();
  const { runId = "" } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const view = new URLSearchParams(location.search).get("view") ?? "timeline";
  const record = useRunRecord(runId, view);
  const redirect = record.detail ? legacyResearchLocation(record.detail.run, location.search, location.hash) : null;
  useEffect(() => { if (redirect) navigate(redirect, { replace: true }); }, [redirect, navigate]);
  if (!record.detail) return <div role={record.error ? "alert" : "status"}>{record.error || t("loading")}{record.error && <button className="button" onClick={() => void record.refresh()}>{t("retryLoad")}</button>}</div>;
  if (redirect) return <div role="status">{t("loading")}</div>;
  if (readingViews.has(view) && !record.detail.run.is_research_node) return <Suspense fallback={<div role="status">{t("loading")}</div>}><LegacyReader record={record} selectedRunId={runId} /></Suspense>;
  return <RunExecutionView key={runId} record={record} diagnostics={view === "diagnostics"} />;
}
