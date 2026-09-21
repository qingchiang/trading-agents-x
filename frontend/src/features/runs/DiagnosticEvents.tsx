import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { RunEvent } from "../../shared/api/client";
import { formatResearchDate } from "../../shared/researchDate";
import JsonRecord from "../../shared/JsonRecord";

export default function DiagnosticEvents({ events }: { events: RunEvent[] }) {
  const { t, i18n } = useTranslation();
  const [query, setQuery] = useState("");
  const [type, setType] = useState("");
  const [page, setPage] = useState(0);
  const types = useMemo(() => [...new Set(events.map(event => event.event_type))].sort(), [events]);
  const filtered = useMemo(() => events.filter(event => (!type || event.event_type === type) && [event.node, event.event_type, eventMessage(event)].join(" ").toLowerCase().includes(query.toLowerCase())), [events, type, query]);
  const lastPage = Math.max(0, Math.ceil(filtered.length / 25) - 1);
  const current = Math.min(page, lastPage);
  return <section className="diagnostic-block" aria-labelledby="diagnostic-events">
    <h2 id="diagnostic-events">{t("diagnosticEvents")}</h2>
    <div className="diagnostic-filters"><label>{t("eventFilter")}<input type="search" value={query} onChange={event => { setQuery(event.target.value); setPage(0); }} /></label>
      <label>{t("eventType")}<select value={type} onChange={event => { setType(event.target.value); setPage(0); }}><option value="">{t("allEvents")}</option>{types.map(value => <option key={value}>{value}</option>)}</select></label>
    </div>
    <p className="secondary-line" role="status">{t("recordCount", { count: filtered.length })}</p>
    {filtered.slice(current * 25, current * 25 + 25).map(event => <article className="diagnostic-event" key={event.sequence}>
      <header><time>{formatResearchDate(event.created_at, i18n.language)}</time><strong>{event.event_type}</strong><code>{event.node ?? t("notRecorded")}</code><span>{t("attempt")} {event.attempt}</span></header>
      <p>{eventMessage(event) || t("notRecorded")}</p>
      <JsonRecord label={`${event.sequence} · ${event.event_type}`} value={event} />
    </article>)}
    {filtered.length > 25 && <div className="pagination"><button className="button" disabled={!current} onClick={() => setPage(current - 1)}>{t("previous")}</button><span>{current + 1} / {lastPage + 1}</span><button className="button" disabled={current === lastPage} onClick={() => setPage(current + 1)}>{t("next")}</button></div>}
    <JsonRecord label={t("diagnosticEvents")} value={events} />
  </section>;
}

function eventMessage(event: RunEvent): string {
  return [event.payload?.message, event.payload?.error_message, event.payload?.reason].filter(value => typeof value === "string").join(" · ");
}
