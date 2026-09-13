import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, type RunDetail, type ResearchArtifact, type RunEvent } from "./api/client";

const terminal = new Set(["succeeded", "failed", "cancelled"]);
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

/** Own requests and event subscriptions per record identity, including rapid A/B/A switches. */
export function useRunRecord(runId: string, view: string) {
  const identity = useMemo(() => ({ runId }), [runId]);
  const current = useRef(identity); current.current = identity;
  const [loaded, setLoaded] = useState<{ identity: object; detail: RunDetail } | null>(null);
  const detail = loaded?.identity === identity ? loaded.detail : null;
  const [artifactState, setArtifacts] = useState<{ identity: object; items: ResearchArtifact[] } | null>(null);
  const [eventState, setEvents] = useState<{ identity: object; items: RunEvent[] } | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const request = useRef(0);
  const sequence = useRef(0);
  const refresh = useCallback(async () => {
    if (current.current !== identity) return;
    const ticket = ++request.current;
    try {
      const value = await api.run(runId);
      if (current.current !== identity || ticket !== request.current) return;
      setLoaded({ identity, detail: value }); setError("");
    } catch (cause) {
      if (current.current === identity && ticket === request.current) setError(String(cause));
    }
  }, [identity, runId]);
  useEffect(() => {
    setError(""); sequence.current = 0; void refresh();
    return () => { request.current++; };
  }, [refresh]);
  useEffect(() => {
    if (!["deliberation", "diagnostics"].includes(view) && !(detail && !detail.run.is_research_node && ["failed", "cancelled"].includes(detail.run.status))) return;
    let active = true;
    void api.artifacts(runId).then(items => { if (active) setArtifacts({ identity, items }); }, cause => { if (active) setError(String(cause)); });
    return () => { active = false; };
  }, [identity, runId, view, revision, detail?.run.is_research_node, detail?.run.status]);
  useEffect(() => {
    if (!detail || (terminal.has(detail.run.status) && !["timeline", "diagnostics", "deliberation"].includes(view))) return;
    const attempt = detail.run.attempt;
    const source = new EventSource(`/api/v1/runs/${runId}/events?after=${sequence.current}`);
    let active = true;
    const receive = (raw: MessageEvent<string>) => {
      if (!active || current.current !== identity) return;
      let event: RunEvent;
      try { event = JSON.parse(raw.data) as RunEvent; } catch { return; }
      sequence.current = Math.max(sequence.current, event.sequence);
      setEvents(previous => {
        const items = previous?.identity === identity ? previous.items : [];
        return { identity, items: items.some(item => item.sequence === event.sequence) ? items : [...items, event] };
      });
      if (event.event_type === "node.completed" || event.event_type === "evidence.sealed" || event.event_type.startsWith("incremental.") || event.event_type.startsWith("run.")) void refresh();
      if (event.event_type === "artifact.created") setRevision(value => value + 1);
      if (event.attempt === attempt && ["run.succeeded", "run.failed", "run.cancelled"].includes(event.event_type)) source.close();
    };
    eventNames.forEach(name => source.addEventListener(name, receive as EventListener));
    source.onerror = () => { if (active) void refresh(); };
    return () => { active = false; source.close(); };
  }, [identity, runId, detail?.run.status, detail?.run.attempt, Boolean(detail), view, refresh]);
  return { detail, artifacts: artifactState?.identity === identity ? artifactState.items : [], events: eventState?.identity === identity ? eventState.items : [], evidence: detail?.result?.evidence ?? null, error, setError: (message: string) => { if (current.current === identity) setError(message); }, refresh };
}
export type RunRecord = ReturnType<typeof useRunRecord>;
