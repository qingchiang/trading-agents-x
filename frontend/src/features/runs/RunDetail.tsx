import { useTranslation } from "react-i18next";
import { useLocation, useParams } from "../../app/router";
import { useRunRecord } from "../../shared/useRunRecord";
import RunExecutionView from "./RunExecutionView";

/** Run routes contain execution history and diagnostics. Research lives in Timeline. */
export default function RunDetail() {
  const { t } = useTranslation();
  const { runId = "" } = useParams();
  const location = useLocation();
  const view = new URLSearchParams(location.search).get("view") === "diagnostics" ? "diagnostics" : "timeline";
  const record = useRunRecord(runId, view);
  if (!record.detail) return <div role={record.error ? "alert" : "status"}>{record.error || t("loading")}{record.error && <button className="button" onClick={() => void record.refresh()}>{t("retryLoad")}</button>}</div>;
  return <RunExecutionView key={runId} record={record} diagnostics={view === "diagnostics"} />;
}
