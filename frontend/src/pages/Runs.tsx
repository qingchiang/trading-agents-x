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
} from "../api/client";
import ConfirmDialog from "../components/ConfirmDialog";
import { InstrumentIdentity } from "../components/Instruments";
import ResearchRatingBadge from "../components/ResearchRatingBadge";
import ResearchKindBadge from "../components/ResearchKindBadge";
import StatusBadge from "../components/StatusBadge";
import { Link, useLocation, useNavigate } from "../router";
import { formatUtcDate, trashDeadline } from "../trash";

const pageSize = 20;
const terminalStatuses = new Set(["succeeded", "failed", "cancelled"]);
const runStatuses = [
  "queued",
  "running",
  "succeeded",
  "failed",
  "cancelled",
] as const;

type TrashState = "active" | "trashed";

export default function Runs() {
  const { t } = useTranslation();
  const location = useLocation();
  const navigate = useNavigate();
  const params = useMemo(
    () => new URLSearchParams(location.search),
    [location.search],
  );
  const trashState: TrashState =
    params.get("trash_state") === "trashed" ? "trashed" : "active";
  const requestedStatus = params.get("status") ?? "";
  const status = runStatuses.includes(
    requestedStatus as (typeof runStatuses)[number],
  )
    ? requestedStatus
    : "";
  const query = params.get("q") ?? "";
  const requestedKind = params.get("research_kind") ?? "";
  const researchKind = requestedKind === "full" || requestedKind === "incremental"
    ? requestedKind
    : "";
  const offset = parseOffset(params.get("offset"));
  const [page, setPage] = useState<RunPage | null>(null);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [qInput, setQInput] = useState(query);
  const [statusInput, setStatusInput] = useState(status);
  const [kindInput, setKindInput] = useState(researchKind);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [confirmTrash, setConfirmTrash] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    setQInput(query);
    setStatusInput(status);
    setKindInput(researchKind);
  }, [query, status, researchKind]);

  const load = useCallback(async () => {
    const requestParams = new URLSearchParams({
      trash_state: trashState,
      limit: String(pageSize),
      offset: String(offset),
    });
    if (query) requestParams.set("q", query);
    if (status) requestParams.set("status", status);
    if (researchKind) requestParams.set("research_kind", researchKind);
    try {
      const next = await api.runs(`?${requestParams}`);
      setPage(next);
      setSelected(new Set());
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("error"));
    }
  }, [trashState, offset, query, status, researchKind, t]);

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
      research_kind: kindInput || null,
      offset: null,
    });
  };

  const eligibleRuns = (page?.items ?? []).filter((run) =>
    !run.is_research_node &&
    (trashState === "trashed" ? true : terminalStatuses.has(run.status)),
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
    setConfirmTrash(false);
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result =
        trashState === "active"
          ? await api.trashRuns(runIds)
          : await api.restoreRuns(runIds);
      setNotice(
        t(
          trashState === "active" ? "runsTrashed" : "runsRestored",
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

      <div className="panel trash-state-tabs" role="tablist">
        {(["active", "trashed"] as const).map((state) => (
          <button
            key={state}
            type="button"
            role="tab"
            aria-selected={trashState === state}
            className={trashState === state ? "active" : ""}
            onClick={() =>
              updateSearch({
                trash_state: state === "active" ? null : state,
                offset: null,
              })
            }
          >
            {t(state === "active" ? "activeRuns" : "trashedRuns")}
          </button>
        ))}
      </div>

      {trashState === "trashed" && (
        <div className="trash-notice trash-retention-notice" role="note">
          <strong>{t("trashRetentionTitle")}</strong>
          <span>
            {retentionPolicyLabel(
              capabilities?.defaults.trash_retention_days ?? 30,
              t,
            )}
          </span>
        </div>
      )}

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
        <label htmlFor="runs-kind">
          {t("researchKind")}
          <select
            id="runs-kind"
            name="research_kind"
            value={kindInput}
            onChange={(event) => setKindInput(event.target.value)}
          >
            <option value="">{t("all")}</option>
            <option value="full">{t("fullResearch")}</option>
            <option value="incremental">{t("incrementalResearch")}</option>
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
              {t(trashState === "active" ? "activeRuns" : "trashedRuns")}
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
              trashState === "active" ? "danger" : "primary"
            }`}
            disabled={busy || selected.size === 0}
            onClick={() => {
              if (trashState === "active") setConfirmTrash(true);
              else void applyLifecycle();
            }}
          >
            {trashState === "active"
              ? t("trashSelected", { count: selected.size })
              : t("restoreSelected", { count: selected.size })}
          </button>
        </div>

        {!page ? (
          <div className="loading">{t("loading")}</div>
        ) : page.items.length === 0 ? (
          <div className="empty-state">
            {t(trashState === "active" ? "noActiveRuns" : "noTrashedRuns")}
          </div>
        ) : (
          <div className="table-wrap">
            <table className="runs-table">
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
                  <th>{t("researchRating")}</th>
                  <th>{t("researchKind")}</th>
                  <th>{t("analysisDate")}</th>
                  <th>{t("status")}</th>
                  <th>
                    {t(
                      trashState === "active"
                        ? "updated"
                        : "permanentDeletion",
                    )}
                  </th>
                  <th>{t("actions")}</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((run) => {
                  const eligible =
                    !run.is_research_node &&
                    (trashState === "trashed" ||
                      terminalStatuses.has(run.status));
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
                          instrumentLocalName={run.instrument_local_name}
                        />
                      </td>
                      <td>
                        <div className="decision-cell">
                          <ResearchRatingBadge rating={run.research_rating} />
                          {run.research_confidence != null && (
                            <small>{t("confidencePercent", { value: Math.round(run.research_confidence * 100) })}</small>
                          )}
                        </div>
                      </td>
                      <td>
                        <div className="run-kind-cell">
                          <ResearchKindBadge
                            kind={run.research_kind}
                            request={run.request}
                            methodSnapshot={run.method_snapshot}
                          />
                        </div>
                      </td>
                      <td>{run.request.analysis_date}</td>
                      <td>
                        <StatusBadge status={run.status} />
                      </td>
                      <td>
                        {trashState === "trashed" && run.trashed_at ? (
                          <TrashDeadlineLabel
                            trashedAt={run.trashed_at}
                            retentionDays={
                              capabilities?.defaults.trash_retention_days ?? 30
                            }
                            t={t}
                          />
                        ) : (
                          formatDate(run.updated_at)
                        )}
                      </td>
                      <td className="run-actions-cell">
                        <Link
                          className="button compact-button"
                          to={`/runs/${run.id}`}
                        >
                          {t("open")}
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
      {confirmTrash && (
        <ConfirmDialog
          title={t("trashDialogTitle", { count: selected.size })}
          confirmLabel={t("confirmTrash")}
          cancelLabel={t("keepRuns")}
          busy={busy}
          onCancel={() => setConfirmTrash(false)}
          onConfirm={() => void applyLifecycle()}
        >
          <p>{t("trashDialogImpact")}</p>
          <p>
            {trashDialogRetention(
              capabilities?.defaults.trash_retention_days ?? 30,
              t,
            )}
          </p>
        </ConfirmDialog>
      )}
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

function retentionPolicyLabel(retentionDays: number, t: TFunction) {
  if (retentionDays === 0) return t("trashRetentionDisabled");
  return t("trashRetentionPolicy", { count: retentionDays });
}

function trashDialogRetention(retentionDays: number, t: TFunction) {
  const deadline = trashDeadline(new Date(), retentionDays);
  if (!deadline) return t("trashRetentionDisabled");
  return t("trashDialogDeletion", {
    date: formatUtcDate(deadline.deletionAt),
    count: retentionDays,
  });
}

function TrashDeadlineLabel({
  trashedAt,
  retentionDays,
  t,
}: {
  trashedAt: string;
  retentionDays: number;
  t: TFunction;
}) {
  const deadline = trashDeadline(trashedAt, retentionDays);
  if (!deadline) {
    return (
      <span className="trash-deadline">
        <strong>{t("permanentCleanupDisabled")}</strong>
        <small>{t("movedToTrashAt", { date: formatDate(trashedAt) })}</small>
      </span>
    );
  }
  return (
    <span className="trash-deadline">
      <strong>{formatUtcDate(deadline.deletionAt)}</strong>
      <small>
        {deadline.due
          ? t("trashCleanupDue")
          : t("trashDaysRemaining", { count: deadline.remainingDays })}
      </small>
    </span>
  );
}
