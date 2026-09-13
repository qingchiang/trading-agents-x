import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type RunLifecyclePreview } from "../api/client";
import ConfirmDialog from "./ConfirmDialog";

const lifecycleMessages: Record<string, string> = {
  "Some selected runs no longer exist.": "lifecycleMissing",
  "Only completed, failed or cancelled tasks can be moved to Trash.": "lifecycleRunning",
  "All affected research must be in Trash before permanent deletion.": "lifecycleActive",
  "restored Full must remain a valid current Full Baseline": "lifecycleInvalidBaseline",
  "an Incremental cannot be restored while its Full remains in Trash": "lifecycleParentTrashed",
  "restored Incremental must retain a valid current Full Baseline": "lifecycleInvalidRelation",
  "restore contains duplicate same-Cycle/cutoff slots": "lifecycleDuplicateDate",
  "restore conflicts with an active slot for the same Cycle/cutoff": "lifecycleDateConflict",
  "The affected research changed. Refresh the preview and confirm again.": "lifecycleScopeChanged",
};

export type LifecycleAction = "trash" | "restore" | "purge";
export default function RunLifecycleDialog({ runIds, action, onClose, onDone }: {
  runIds: string[]; action: LifecycleAction; onClose: () => void; onDone: (changed: number) => void;
}) {
  const { t } = useTranslation();
  const [preview, setPreview] = useState<RunLifecyclePreview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const [revision, setRevision] = useState(0);
  const [replacements, setReplacements] = useState<Record<string, string>>({});
  const key = runIds.join("|");
  useEffect(() => {
    let active = true;
    setPreview(null); setReplacements({});
    api.previewLifecycle(runIds, action).then(value => { if (active) setPreview(value); }, cause => { if (active) setError(String(cause)); });
    return () => { active = false; };
  }, [key, action, revision]);
  const missingChoice = Object.keys(preview?.primary_replacements ?? {}).some(id => !replacements[id]);
  const submit = async () => {
    if (!preview || missingChoice || preview.blocked_reasons?.length || lock.current) return;
    lock.current = true; setBusy(true); setError("");
    try {
      const result = action === "trash" ? await api.trashRuns(runIds, replacements, preview.affected_run_ids)
        : action === "restore" ? await api.restoreRuns(runIds, preview.affected_run_ids)
        : await api.purgeRuns(runIds, preview.affected_run_ids);
      onDone(result.changed);
    } catch (cause) { setPreview(null); setError(cause instanceof Error ? cause.message : String(cause)); setRevision(value => value + 1); }
    finally { lock.current = false; setBusy(false); }
  };
  return <ConfirmDialog title={t(`lifecycleTitle_${action}`)} confirmLabel={t(action === "trash" ? "confirmTimelineTrash" : action === "restore" ? "restore" : "confirmPurge")} cancelLabel={t("cancel")} busy={busy} confirmDisabled={!preview || missingChoice || Boolean(preview.blocked_reasons?.length)} onCancel={onClose} onConfirm={() => void submit()}>
    <p>{t(action === "purge" ? "purgeResearchImpact" : "lifecyclePreviewHint")}</p>
    {error && <p role="alert">{t(lifecycleMessages[error] ?? error, { defaultValue: error })} <button className="button" onClick={() => setRevision(value => value + 1)}>{t("retryLoad")}</button></p>}
    {!preview ? <p role="status">{t("loading")}</p> : <>
      <p><strong>{t("affectedResearch", { count: preview.affected_runs.length })}</strong></p>
      <ul className="lifecycle-impact-list">{preview.affected_runs.map(run => <li key={run.id}>{run.instrument_name ?? run.request.ticker} · {run.request.analysis_date} · {t(run.research_kind === "incremental" ? "incrementalResearch" : "fullResearch")}</li>)}</ul>
      {preview.blocked_reasons?.map(reason => <p role="alert" key={reason}>{t(lifecycleMessages[reason] ?? reason, { defaultValue: reason })}</p>)}
      {Object.entries(preview.primary_replacements ?? {}).map(([id, candidates]) => <label className="replacement-choice" key={id}>{t("replacementPrimaryCycle")}<select value={replacements[id] ?? ""} onChange={event => setReplacements(current => ({ ...current, [id]: event.target.value }))}><option value="">{t("selectReplacementCycle")}</option>{candidates.map(candidate => <option key={candidate.id} value={candidate.id}>{candidate.analysis_date} · {candidate.rating ?? "—"}</option>)}</select></label>)}
    </>}
  </ConfirmDialog>;
}
