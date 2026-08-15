import { useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  api,
  type Capabilities,
  type EvidenceBundle,
  type ResearchArtifact,
  type RunDetail,
  type RunEvent,
} from "../../api/client";

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
];

export type RunDetailPageViewModel = {
  artifacts: ResearchArtifact[];
  capabilities: Capabilities | null;
  detail: RunDetail | null;
  error: string;
  events: RunEvent[];
  evidence: EvidenceBundle | null;
};

export function useRunDetailPage(runId: string) {
  const { t } = useTranslation();
  const [model, setModel] = useState<RunDetailPageViewModel>({
    artifacts: [],
    capabilities: null,
    detail: null,
    error: "",
    events: [],
    evidence: null,
  });

  const setError = useCallback((error: string) => {
    setModel((current) => ({ ...current, error }));
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [detail, artifacts] = await Promise.all([
        api.run(runId),
        api.artifacts(runId),
      ]);
      let evidence = detail.result?.evidence ?? null;
      let error = "";
      if (detail.evidence_status.status === "sealed") {
        try {
          evidence = await api.evidence(runId);
        } catch (cause) {
          error = cause instanceof Error ? cause.message : t("error");
        }
      }
      setModel((current) => ({
        ...current,
        artifacts,
        detail,
        error,
        evidence,
      }));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("error"));
    }
  }, [runId, setError, t]);

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
      setModel((current) => ({
        ...current,
        events: current.events.some((item) => item.sequence === event.sequence)
          ? current.events
          : [...current.events, event],
      }));
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
      .then((capabilities) => {
        if (active) {
          setModel((current) => ({ ...current, capabilities }));
        }
      })
      .catch(() => {
        if (active) {
          setModel((current) => ({ ...current, capabilities: null }));
        }
      });
    return () => {
      active = false;
    };
  }, []);

  return { model, refresh, setError };
}
