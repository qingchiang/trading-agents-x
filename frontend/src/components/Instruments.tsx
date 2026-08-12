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
          label={instrumentLabel(
            instrument.instrument_local_name,
            instrument.instrument_name,
          ) ?? instrument.ticker}
        />
      ))}
    </datalist>
  );
}

export function InstrumentIdentity({
  ticker,
  instrumentName,
  instrumentLocalName,
  prominent = false,
}: {
  ticker: string;
  instrumentName?: string | null;
  instrumentLocalName?: string | null;
  prominent?: boolean;
}) {
  const [primaryName, secondaryName] = distinctNames(
    instrumentLocalName,
    instrumentName,
  );
  const content = (
    <>
      {prominent ? (
        <h1 className="ticker">{ticker}</h1>
      ) : (
        <strong className="ticker">{ticker}</strong>
      )}
      {primaryName && (
        <span className="instrument-primary-name" title={primaryName}>
          {primaryName}
        </span>
      )}
      {secondaryName && (
        <span className="instrument-secondary-name" title={secondaryName}>
          {secondaryName}
        </span>
      )}
    </>
  );
  if (prominent) {
    return <div className="instrument-identity prominent">{content}</div>;
  }
  return <span className="instrument-identity">{content}</span>;
}

function instrumentLabel(
  localName?: string | null,
  generalName?: string | null,
): string | null {
  const names = distinctNames(localName, generalName);
  return names.length ? names.join(" · ") : null;
}

function distinctNames(
  localName?: string | null,
  generalName?: string | null,
): string[] {
  const values = [localName, generalName]
    .map((value) => value?.trim())
    .filter((value): value is string => Boolean(value));
  const seen = new Set<string>();
  return values.filter((value) => {
    const key = value
      .normalize("NFKC")
      .toLocaleLowerCase()
      .replace(/[\s\p{P}\p{S}]+/gu, "");
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}
