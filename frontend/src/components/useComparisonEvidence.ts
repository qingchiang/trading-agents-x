import { useEffect, useMemo, useRef, useState } from "react";
import { api, type EvidenceBundle, type ResearchNodeComparison } from "../api/client";
import { buildEvidenceReferenceIndex } from "../evidence";

/** A side can resolve only its own bundle and its direct Full baseline. */
export function useComparisonEvidence(side: ResearchNodeComparison["sides"][number]) {
  const cache = useRef(new Map<string, EvidenceBundle>());
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState<{ key: string; current: EvidenceBundle | null; baseline: EvidenceBundle | null; error: string; loading: boolean }>();
  const baselineId = side.research_kind === "incremental" ? side.cycle_id : "";
  const key = `${side.node_id}:${baselineId}:${side.lifecycle_state}`;
  useEffect(() => {
    let active = true;
    const ids = [side.node_id, ...(baselineId ? [baselineId] : [])];
    setState({ key, current: cache.current.get(ids[0]) ?? null, baseline: cache.current.get(ids[1]) ?? null, error: "", loading: true });
    void Promise.allSettled(ids.map(async id => {
      if (cache.current.has(id)) return cache.current.get(id)!;
      const bundle = await api.evidence(id);
      if (!bundle?.items) throw new Error("Evidence unavailable");
      if (active) cache.current.set(id, bundle);
      return bundle;
    })).then(results => {
      if (!active) return;
      setState({ key, current: results[0].status === "fulfilled" ? results[0].value : null,
        baseline: results[1]?.status === "fulfilled" ? results[1].value : null,
        error: results.some(result => result.status === "rejected") ? "evidenceReferenceUnavailable" : "", loading: false });
    });
    return () => { active = false; };
  }, [key, side.node_id, baselineId, revision]);
  const current = state?.key === key ? state : undefined;
  const index = useMemo(() => buildEvidenceReferenceIndex(current?.current ?? null, current?.baseline ?? null), [current?.current, current?.baseline]);
  return { index, error: current?.error, loading: !current || current.loading, retry: () => setRevision(value => value + 1) };
}
