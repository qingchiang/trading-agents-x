import { useCallback, useEffect, useState } from "react";
import { api } from "./api/client";

export function useMarketDate(instrument?: string) {
  const [state, setState] = useState<{ instrument?: string; date: string | null; error: string }>({ date: null, error: "" });
  const [revision, setRevision] = useState(0);
  const retry = useCallback(() => setRevision(value => value + 1), []);
  useEffect(() => {
    let active = true;
    let generation = 0;
    let timer: number | undefined;
    if (!instrument) return;
    const refresh = async () => {
      const request = ++generation;
      window.clearTimeout(timer);
      setState({ instrument, date: null, error: "" });
      try {
        const context = await api.analysisCutoffContext(instrument);
        if (!active || request !== generation) return;
        setState({ instrument, date: context.max_analysis_date, error: "" });
        // Use the server validity interval, not the browser's wall clock.
        const validity = Date.parse(context.valid_until) - Date.parse(context.observed_at);
        timer = window.setTimeout(() => { void refresh(); }, Math.min(2_147_483_647, Math.max(1000, validity)));
      } catch (error) {
        if (active && request === generation) setState({ instrument, date: null, error: error instanceof Error ? error.message : String(error) });
      }
    };
    const visible = () => { if (document.visibilityState === "visible") void refresh(); };
    void refresh();
    document.addEventListener("visibilitychange", visible);
    return () => { active = false; window.clearTimeout(timer); document.removeEventListener("visibilitychange", visible); };
  }, [instrument, revision]);
  return { ...(state.instrument === instrument ? state : { date: null, error: "" }), retry };
}
