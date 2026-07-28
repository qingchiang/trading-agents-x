import { FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type MemoryEntry } from "../api/client";
import Markdown from "../components/Markdown";
import StatusBadge from "../components/StatusBadge";

export default function Memory() {
  const { t } = useTranslation();
  const [entries, setEntries] = useState<MemoryEntry[]>([]);
  const initialParams = new URLSearchParams(window.location.search);
  const [q, setQ] = useState(() => initialParams.get("q") ?? "");
  const [ticker, setTicker] = useState(
    () => initialParams.get("ticker") ?? "",
  );
  const [market, setMarket] = useState(
    () => initialParams.get("market") ?? "",
  );
  const [status, setStatus] = useState(
    () => initialParams.get("status") ?? "",
  );
  const [error, setError] = useState("");

  const load = async (query = "") => {
    try {
      const rows = await api.memory(query);
      setEntries(rows);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("error"));
    }
  };
  useEffect(() => {
    void load(window.location.search);
  }, []);
  useEffect(() => {
    if (!entries.length || !window.location.hash) return;
    let targetId = window.location.hash.slice(1);
    try {
      targetId = decodeURIComponent(targetId);
    } catch {
      return;
    }
    const target = document.getElementById(targetId);
    target?.focus();
    target?.scrollIntoView?.({ behavior: "smooth", block: "center" });
  }, [entries]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const params = new URLSearchParams();
    if (q.trim()) params.set("q", q.trim());
    if (ticker.trim()) params.set("ticker", ticker.trim());
    if (market.trim()) params.set("market", market.trim());
    if (status) params.set("status", status);
    const query = params.size ? `?${params}` : "";
    window.history.replaceState(null, "", `/memory${query}`);
    void load(query);
  };

  return (
    <section>
      <header className="page-header">
        <div>
          <p className="eyebrow">{t("deterministicFeedback")}</p>
          <h1>{t("memory")}</h1>
          <p className="subtitle">{t("feedbackHint")}</p>
        </div>
      </header>
      <form className="panel filter-bar" onSubmit={submit}>
        <label>
          {t("memorySearch")}
          <input
            value={q}
            onChange={(event) => setQ(event.target.value)}
            placeholder={t("memorySearchPlaceholder")}
          />
        </label>
        <label>
          {t("ticker")}
          <input value={ticker} onChange={(event) => setTicker(event.target.value)} />
        </label>
        <label>
          {t("market")}
          <input
            value={market}
            onChange={(event) => setMarket(event.target.value)}
            placeholder="Asia/Tokyo"
          />
        </label>
        <label>
          {t("status")}
          <select value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="">{t("all")}</option>
            <option value="pending">{t("statusPending")}</option>
            <option value="resolved">{t("statusResolved")}</option>
          </select>
        </label>
        <button className="button primary">{t("apply")}</button>
      </form>
      {error && <div className="alert">{error}</div>}
      <div className="memory-list">
        {entries.map((entry) => (
          <article
            className="panel memory-card"
            id={`memory-${entry.run_id}`}
            key={entry.run_id}
            tabIndex={-1}
          >
            <div className="memory-meta">
              <div>
                <strong className="ticker">{entry.ticker}</strong>
                <span>{entry.analysis_date} · {entry.market || "—"}</span>
              </div>
              <StatusBadge status={entry.outcome.status} />
            </div>
            <div className="memory-decision">
              <strong>{entry.decision.rating}</strong>
              <span>{Math.round(entry.decision.confidence * 100)}%</span>
              <Markdown>{entry.decision.thesis}</Markdown>
            </div>
            {entry.outcome.status === "resolved" && (
              <div className="returns">
                <span>
                  {t("rawReturn")}{" "}
                  <strong>{percent(entry.outcome.raw_return)}</strong>
                </span>
                <span>
                  {t("alphaReturn")}{" "}
                  <strong>{percent(entry.outcome.alpha_return)}</strong>
                </span>
                <span>
                  {entry.outcome.observation_start} → {entry.outcome.observation_end}
                </span>
              </div>
            )}
            {entry.reflection && (
              <blockquote>
                <strong>{t("reflection")}</strong>
                <Markdown>{entry.reflection}</Markdown>
              </blockquote>
            )}
          </article>
        ))}
        {entries.length === 0 && (
          <div className="empty-state">{t("noMemory")}</div>
        )}
      </div>
    </section>
  );
}

function percent(value: number | null) {
  return value == null ? "—" : `${value >= 0 ? "+" : ""}${(value * 100).toFixed(1)}%`;
}
