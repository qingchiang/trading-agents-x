import { useEffect, useState } from "react";

import { api, type RecentInstrument } from "../api/client";

export const recentInstrumentListId = "recent-instruments";

export function useRecentInstruments(limit = 50) {
  const [instruments, setInstruments] = useState<RecentInstrument[]>([]);

  useEffect(() => {
    let active = true;
    void api
      .recentInstruments(limit)
      .then((items) => {
        if (active) setInstruments(items);
      })
      .catch(() => {
        if (active) setInstruments([]);
      });
    return () => {
      active = false;
    };
  }, [limit]);

  return instruments;
}

export function RecentInstrumentDatalist({
  instruments,
}: {
  instruments: RecentInstrument[];
}) {
  return (
    <datalist id={recentInstrumentListId}>
      {instruments.map((instrument) => (
        <option
          key={instrument.ticker}
          value={instrument.ticker}
          label={instrument.instrument_name ?? instrument.ticker}
        />
      ))}
    </datalist>
  );
}

export function InstrumentIdentity({
  ticker,
  instrumentName,
}: {
  ticker: string;
  instrumentName?: string | null;
}) {
  return (
    <span className="instrument-identity">
      <strong className="ticker">{ticker}</strong>
      {instrumentName && (
        <span className="instrument-name">{instrumentName}</span>
      )}
    </span>
  );
}
