import { useEffect, useState } from "react";
import { api } from "./api/client";

export function useMarketDate(instrument?: string) {
  const [state, setState] = useState<{ instrument?: string; date: string | null; error: string }>({ date: null, error: "" });
  useEffect(() => {
    let active = true;
    let timer: number | undefined;
    if (!instrument) return;
    const refresh = async () => {
      window.clearTimeout(timer);
      try {
        const context = await api.analysisCutoffContext(instrument);
        if (!active) return;
        setState({ instrument, date: context.max_analysis_date, error: "" });
        timer = window.setTimeout(() => { setState({ instrument, date: null, error: "" }); void refresh(); },
          Math.min(2_147_483_647, Math.max(1000, Date.parse(context.valid_until) - Date.now())));
      } catch (error) {
        if (active) setState({ instrument, date: null, error: error instanceof Error ? error.message : String(error) });
      }
    };
    const visible = () => { if (document.visibilityState === "visible") void refresh(); };
    void refresh();
    document.addEventListener("visibilitychange", visible);
    return () => { active = false; window.clearTimeout(timer); document.removeEventListener("visibilitychange", visible); };
  }, [instrument]);
  return state.instrument === instrument ? state : { date: null, error: "" };
}
