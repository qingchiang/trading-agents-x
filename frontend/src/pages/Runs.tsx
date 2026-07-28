import {
  FormEvent,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";

import {
  api,
  type Capabilities,
  type RunPage,
  type RunView,
} from "../api/client";
import { InstrumentIdentity } from "../components/Instruments";
import StatusBadge from "../components/StatusBadge";
import { Link, useLocation, useNavigate } from "../router";

const pageSize = 20;
const terminalStatuses = new Set(["succeeded", "failed", "cancelled"]);
const runStatuses = [
  "queued",
  "running",
  "succeeded",
  "failed",
  "cancelled",
] as const;

type ArchiveState = "active" | "archived";

export default function Runs() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const params = useMemo(
    () => new URLSearchParams(location.search),
    [location.search],
  );
  const archiveState: ArchiveState =
    params.get("archive_state") === "archived" ? "archived" : "active";
  const requestedStatus = params.get("status") ?? "";
  const status = runStatuses.includes(
    requestedStatus as (typeof runStatuses)[number],
  )
    ? requestedStatus
    : "";
  const query = params.get("q") ?? "";
  const offset = parseOffset(params.get("offset"));
  const [page, setPage] = useState<RunPage | null>(null);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [qInput, setQInput] = useState(query);
  const [statusInput, setStatusInput] = useState(status);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    setQInput(query);
    setStatusInput(status);
  }, [query, status]);

  const load = useCallback(async () => {
    const requestParams = new URLSearchParams({
      archive_state: archiveState,
      limit: String(pageSize),
      offset: String(offset),
    });
    if (query) requestParams.set("q", query);
    if (status) requestParams.set("status", status);
    try {
      const next = await api.runs(`?${requestParams}`);
      setPage(next);
      setSelected(new Set());
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("error"));
    }
  }, [archiveState, offset, query, status, t]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    let active = true;
    void api
      .capabilities()
      .then((value) => {
        if (active) setCapabilities(value);
      })
      .catch(() => {
        if (active) setCapabilities(null);
      });
    return () => {
      active = false;
    };
  }, []);

  const updateSearch = (updates: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(updates)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    const search = next.size ? `?${next}` : "";
    navigate(`/runs${search}`, { replace: true });
  };

  const applyFilters = (event: FormEvent) => {
    event.preventDefault();
    updateSearch({
      q: qInput.trim() || null,
      status: statusInput || null,
      offset: null,
    });
  };

  const eligibleRuns = (page?.items ?? []).filter((run) =>
    archiveState === "archived" ? true : terminalStatuses.has(run.status),
  );
  const allEligibleSelected =
    eligibleRuns.length > 0 &&
    eligibleRuns.every((run) => selected.has(run.id));

  const toggleRun = (runId: string) => {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(runId)) next.delete(runId);
      else next.add(runId);
      return next;
    });
  };

  const togglePage = () => {
    setSelected(
      allEligibleSelected
        ? new Set()
        : new Set(eligibleRuns.map((run) => run.id)),
    );
  };

  const applyLifecycle = async () => {
    const runIds = [...selected];
    if (!runIds.length) return;
    if (
      archiveState === "active" &&
      !window.confirm(
        t("archiveConfirm", {
          count: runIds.length,
          purge: purgeEstimate(
            capabilities?.defaults.archive_retention_days ?? 30,
            t,
          ),
        }),
      )
    ) {
      return;
    }
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result =
        archiveState === "active"
          ? await api.archiveRuns(runIds)
          : await api.restoreRuns(runIds);
      setNotice(
        t(
          archiveState === "active" ? "runsArchived" : "runsRestored",
          { count: result.changed },
        ),
      );
      if (offset > 0 && runIds.length >= (page?.items.length ?? 0)) {
        updateSearch({ offset: String(Math.max(0, offset - pageSize)) });
      } else {
        await load();
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("error"));
    } finally {
      setBusy(false);
    }
  };

  const start = page?.total ? offset + 1 : 0;
  const end = page ? Math.min(offset + page.items.length, page.total) : 0;

  return (
    <section>
      <header className="page-header">
        <div>
          <p className="eyebrow">{t("runHistory")}</p>
          <h1>{t("runManagement")}</h1>
          <p className="subtitle">{t("runManagementHint")}</p>
        </div>
        <Link className="button primary" to="/runs/new">
          + {t("newRun")}
        </Link>
      </header>

      <div className="panel archive-state-tabs" role="tablist">
        {(["active", "archived"] as const).map((state) => (
          <button
            key={state}
            type="button"
            role="tab"
            aria-selected={archiveState === state}
            className={archiveState === state ? "active" : ""}
            onClick={() =>
              updateSearch({
                archive_state: state === "active" ? null : state,
                offset: null,
              })
            }
          >
            {t(state === "active" ? "activeRuns" : "archivedRuns")}
          </button>
        ))}
      </div>

      <form className="panel filter-bar run-filter-bar" onSubmit={applyFilters}>
        <label htmlFor="runs-search">
          {t("runSearch")}
          <input
            id="runs-search"
            name="q"
            autoComplete="on"
            value={qInput}
            onChange={(event) => setQInput(event.target.value)}
            placeholder={t("runSearchPlaceholder")}
          />
        </label>
        <label htmlFor="runs-status">
          {t("status")}
          <select
            id="runs-status"
            name="status"
            value={statusInput}
            onChange={(event) => setStatusInput(event.target.value)}
          >
            <option value="">{t("all")}</option>
            {runStatuses.map((value) => (
              <option key={value} value={value}>
                {t(statusLabel(value))}
              </option>
            ))}
          </select>
        </label>
        <button className="button primary">{t("apply")}</button>
      </form>

      {error && <div className="alert">{error}</div>}
      {notice && <div className="notice">{notice}</div>}

      <article className="panel run-management-panel">
        <div className="panel-header">
          <div>
            <p className="eyebrow">
              {t(archiveState === "active" ? "activeRuns" : "archivedRuns")}
            </p>
            <h2>
              {t("runRange", {
                start,
                end,
                total: page?.total ?? 0,
              })}
            </h2>
          </div>
          <button
            type="button"
            className={`button ${
              archiveState === "active" ? "danger" : "primary"
            }`}
            disabled={busy || selected.size === 0}
            onClick={() => void applyLifecycle()}
          >
            {archiveState === "active"
              ? t("archiveSelected", { count: selected.size })
              : t("restoreSelected", { count: selected.size })}
          </button>
        </div>

        {!page ? (
          <div className="loading">{t("loading")}</div>
        ) : page.items.length === 0 ? (
          <div className="empty-state">
            {t(archiveState === "active" ? "noActiveRuns" : "noArchivedRuns")}
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th className="selection-cell">
                    <input
                      type="checkbox"
                      aria-label={t("selectCurrentPage")}
                      checked={allEligibleSelected}
                      onChange={togglePage}
                    />
                  </th>
                  <th>{t("ticker")}</th>
                  <th>{t("profile")}</th>
                  <th>{t("analysisDate")}</th>
                  <th>{t("status")}</th>
                  <th>
                    {t(archiveState === "active" ? "updated" : "archivedAt")}
                  </th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {page.items.map((run) => {
                  const eligible =
                    archiveState === "archived" ||
                    terminalStatuses.has(run.status);
                  return (
                    <tr key={run.id}>
                      <td className="selection-cell">
                        <input
                          type="checkbox"
                          aria-label={t("selectRun", {
                            ticker: run.request.ticker,
                          })}
                          disabled={!eligible}
                          checked={selected.has(run.id)}
                          onChange={() => toggleRun(run.id)}
                        />
                      </td>
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
                      <td>
                        {formatDate(
                          archiveState === "archived"
                            ? (run.archived_at ?? run.updated_at)
                            : run.updated_at,
                        )}
                      </td>
                      <td className="right">
                        <Link className="text-link" to={`/runs/${run.id}`}>
                          {t("open")} →
                        </Link>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        <div className="pagination">
          <button
            type="button"
            className="button"
            disabled={offset === 0}
            onClick={() =>
              updateSearch({
                offset: String(Math.max(0, offset - pageSize)),
              })
            }
          >
            ← {t("previous")}
          </button>
          <span>{t("runRange", { start, end, total: page?.total ?? 0 })}</span>
          <button
            type="button"
            className="button"
            disabled={!page || offset + page.items.length >= page.total}
            onClick={() =>
              updateSearch({ offset: String(offset + pageSize) })
            }
          >
            {t("next")} →
          </button>
        </div>
      </article>
    </section>
  );
}

function parseOffset(value: string | null) {
  const parsed = Number.parseInt(value ?? "0", 10);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : 0;
}

function statusLabel(status: (typeof runStatuses)[number]) {
  return `status${status[0].toUpperCase()}${status.slice(1)}`;
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function purgeEstimate(retentionDays: number, t: TFunction) {
  if (retentionDays === 0) return t("permanentCleanupDisabled");
  const date = new Date();
  date.setUTCDate(date.getUTCDate() + retentionDays);
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "long",
    day: "2-digit",
    timeZone: "UTC",
  }).format(date);
}
