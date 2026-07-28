import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type Health, type RunView } from "../api/client";
import { InstrumentIdentity } from "../components/Instruments";
import StatusBadge from "../components/StatusBadge";
import { Link } from "../router";

export default function Dashboard() {
  const { t } = useTranslation();
  const [health, setHealth] = useState<Health | null>(null);
  const [runs, setRuns] = useState<RunView[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    let mounted = true;
    const refresh = async () => {
      try {
        const [nextHealth, nextRuns] = await Promise.all([
          api.health(),
          api.runs("?limit=20"),
        ]);
        if (mounted) {
          setHealth(nextHealth);
          setRuns(nextRuns.items);
          setError("");
        }
      } catch (cause) {
        if (mounted) {
          setError(cause instanceof Error ? cause.message : t("error"));
        }
      }
    };
    void refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => {
      mounted = false;
      window.clearInterval(timer);
    };
  }, [t]);

  const cards = [
    {
      label: t("queue"),
      value: health?.queue.queued ?? "—",
      tone: "amber",
    },
    {
      label: t("running"),
      value: health?.queue.running ?? "—",
      tone: "blue",
    },
    {
      label: t("pendingOutcomes"),
      value: health?.queue.pending_outcomes ?? "—",
      tone: "violet",
    },
  ];

  return (
    <section>
      <header className="page-header">
        <div>
          <p className="eyebrow">{t("controlPlane")}</p>
          <h1>{t("dashboard")}</h1>
          <p className="subtitle">{t("brandTagline")}</p>
        </div>
        <div className={`health ${health?.status === "ok" ? "ok" : ""}`}>
          <i />
          {health?.status === "ok" ? t("healthy") : t("disconnected")}
        </div>
      </header>
      {error && <div className="alert">{error}</div>}
      <div className="metric-grid">
        {cards.map((card) => (
          <article className={`metric-card ${card.tone}`} key={card.label}>
            <span>{card.label}</span>
            <strong>{card.value}</strong>
            <div className="metric-line" />
          </article>
        ))}
      </div>
      <article className="panel">
        <div className="panel-header">
          <div>
            <p className="eyebrow">{t("runHistory")}</p>
            <h2>{t("recentRuns")}</h2>
          </div>
          <div className="action-row">
            <Link className="button" to="/runs">
              {t("manageRuns")}
            </Link>
            <Link className="button primary" to="/runs/new">
              + {t("newRun")}
            </Link>
          </div>
        </div>
        {runs.length === 0 ? (
          <div className="empty-state">{t("noRuns")}</div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("ticker")}</th>
                  <th>{t("profile")}</th>
                  <th>{t("analysisDate")}</th>
                  <th>{t("status")}</th>
                  <th>{t("updated")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {runs.map((run) => (
                  <tr key={run.id}>
                    <td>
                      <InstrumentIdentity
                        ticker={run.request.ticker}
                        instrumentName={run.instrument_name}
                      />
                    </td>
                    <td className="capitalize">{run.request.profile}</td>
                    <td>{run.request.analysis_date}</td>
                    <td>
                      <StatusBadge status={run.status} />
                    </td>
                    <td>{formatDate(run.updated_at)}</td>
                    <td className="right">
                      <Link className="text-link" to={`/runs/${run.id}`}>
                        {t("open")} →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </article>
    </section>
  );
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}
