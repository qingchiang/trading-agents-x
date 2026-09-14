import { useTranslation } from "react-i18next";
import type { RunDetail, RunEvent, ResearchArtifact } from "../api/client";
import type { EvidenceReferenceIndex } from "../evidence";
import NumericAuditAppendixView from "./NumericAuditAppendixView";
import RunMetricsPanel from "./RunMetricsPanel";

export default function RunDiagnostics({ detail, events, artifacts, evidenceIndex, onEvidence }: {
  detail: RunDetail; events: RunEvent[]; artifacts: ResearchArtifact[];
  evidenceIndex: EvidenceReferenceIndex; onEvidence: (ref: string) => void;
}) {
  const { t } = useTranslation();
  const decision = detail.result?.decision;
  return <section className="diagnostics-view" id="run-view-diagnostics" aria-labelledby="run-tab-diagnostics" role="region">
    <h2>{t("diagnostics")}</h2>
    <RunMetricsPanel metrics={detail.run.metrics} attempts={detail.attempts ?? []} events={events} artifacts={artifacts} />
    <NumericAuditAppendixView appendix={detail.result?.numeric_audit}
      calculationRecords={decision?.calculation_records ?? []}
      calculationUses={new Map()}
      evidenceIndex={evidenceIndex} onEvidence={onEvidence} />
    {[
      ["runId", detail.run.id], ["methodSnapshot", detail.run.method_snapshot],
      ["configuration", detail.run.config_snapshot], ["structuredRecoveries", detail.result?.recoveries],
      ["reports", detail.result?.reports], ["reassessment", detail.research_node],
      ["evidence", detail.result?.evidence], ["researchArtifacts", artifacts], ["liveEvents", events],
    ].map(([key, value]) => <details className="diagnostic-section" key={String(key)}>
      <summary>{t(String(key))}</summary><pre>{JSON.stringify(value ?? null, null, 2)}</pre>
    </details>)}
  </section>;
}
