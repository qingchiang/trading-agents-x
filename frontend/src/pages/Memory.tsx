import { FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type MemoryEntry } from "../api/client";
import {
  InstrumentIdentity,
  RecentInstrumentDatalist,
  recentInstrumentListId,
  useRecentInstruments,
} from "../components/Instruments";
import Markdown from "../components/Markdown";
import StatusBadge from "../components/StatusBadge";
import { Link } from "../router";

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
  const recentInstruments = useRecentInstruments();

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
            id="memory-search"
            name="q"
            autoComplete="on"
            value={q}
            onChange={(event) => setQ(event.target.value)}
            placeholder={t("memorySearchPlaceholder")}
          />
        </label>
        <label>
          {t("ticker")}
          <input
            id="memory-ticker"
            name="ticker"
            autoComplete="on"
            list={recentInstrumentListId}
            spellCheck={false}
            value={ticker}
            onChange={(event) => setTicker(event.target.value)}
          />
          <RecentInstrumentDatalist instruments={recentInstruments} />
        </label>
        <label>
          {t("market")}
          <input
            id="memory-market"
            name="market"
            autoComplete="on"
            value={market}
            onChange={(event) => setMarket(event.target.value)}
            placeholder="Asia/Tokyo"
          />
        </label>
        <label>
          {t("status")}
          <select
            id="memory-status"
            name="status"
            value={status}
            onChange={(event) => setStatus(event.target.value)}
          >
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
                <Link
                  className="memory-title-link"
                  to={runDecisionPath(entry.run_id)}
                  aria-label={`${t("openResearchDecision")} ${entry.ticker}`}
                >
                  <InstrumentIdentity
                    ticker={entry.ticker}
                    instrumentName={entry.instrument_name}
                    instrumentLocalName={entry.instrument_local_name}
                  />
                </Link>
                <div className="memory-run-context">
                  <span>{entry.analysis_date}</span>
                  <span>{entry.market || "—"}</span>
                  <span
                    className="memory-profile"
                    title={t(profileDescriptionKey(entry.profile))}
                  >
                    {t(entry.profile)}
                  </span>
                </div>
              </div>
              <div className="memory-actions">
                <StatusBadge status={entry.outcome.status} />
                <Link
                  className="button compact-button"
                  to={runDecisionPath(entry.run_id)}
                >
                  {t("openResearchDecision")}
                </Link>
              </div>
            </div>
            <div className="memory-decision">
              <strong>{entry.decision.rating}</strong>
              <span>{Math.round(entry.decision.confidence * 100)}%</span>
              <Markdown>{entry.decision.thesis}</Markdown>
            </div>
            <details className="memory-decision-details">
              <summary>{t("decisionDetails")}</summary>
              <div className="memory-decision-details-body">
                <section className="memory-scenarios">
                  <h3>{t("scenarios")}</h3>
                  <div className="memory-scenario-grid">
                    {entry.decision.scenarios.map((scenario) => (
                      <article
                        className={`memory-scenario scenario-${scenario.kind}`}
                        key={scenario.kind}
                      >
                        <strong>{t(scenarioLabelKey(scenario.kind))}</strong>
                        <Markdown>{scenario.outcome}</Markdown>
                        <MemoryDecisionList
                          title={t("coreAssumptions")}
                          items={scenario.core_assumptions}
                        />
                      </article>
                    ))}
                  </div>
                </section>
                <div className="memory-decision-grid">
                  <MemoryDecisionList
                    title={t("catalysts")}
                    items={entry.decision.catalysts ?? []}
                    emptyLabel={t("noCatalystsIdentified")}
                  />
                  <MemoryDecisionList
                    title={t("risks")}
                    items={entry.decision.risks ?? []}
                  />
                  <MemoryDecisionList
                    title={t("invalidation")}
                    items={entry.decision.invalidation_conditions ?? []}
                  />
                  <div className="memory-decision-field">
                    <strong>{t("horizon")}</strong>
                    <Markdown>{entry.decision.time_horizon || "—"}</Markdown>
                  </div>
                  <MemoryDecisionList
                    title={t("unresolvedQuestions")}
                    items={entry.decision.unresolved_questions ?? []}
                    emptyLabel={t("noneRecorded")}
                  />
                </div>
              </div>
            </details>
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

function MemoryDecisionList({
  title,
  items,
  emptyLabel = "—",
}: {
  title: string;
  items: string[];
  emptyLabel?: string;
}) {
  return (
    <div className="memory-decision-field">
      <strong>{title}</strong>
      {items.length ? (
        <ul>
          {items.map((item, index) => (
            <li key={`${index}-${item}`}>
              <Markdown>{item}</Markdown>
            </li>
          ))}
        </ul>
      ) : (
        <span>{emptyLabel}</span>
      )}
    </div>
  );
}

function percent(value: number | null) {
  return value == null ? "—" : `${value >= 0 ? "+" : ""}${(value * 100).toFixed(1)}%`;
}

function runDecisionPath(runId: string): string {
  return `/runs/${encodeURIComponent(runId)}?view=decision`;
}

function profileDescriptionKey(profile: MemoryEntry["profile"]): string {
  return {
    fast: "profileFastDesc",
    standard: "profileStandardDesc",
    deep: "profileDeepDesc",
  }[profile];
}

function scenarioLabelKey(
  kind: MemoryEntry["decision"]["scenarios"][number]["kind"],
): string {
  return {
    base: "baseScenario",
    bull: "bullScenario",
    bear: "bearScenario",
  }[kind];
}
