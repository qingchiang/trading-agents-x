import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import {
  type EvidenceBundle,
  type RunDetail as RunDetailType,
} from "../../api/client";
import EvidenceTableView from "../../components/EvidenceTableView";
import Markdown from "../../components/Markdown";
import {
  type EvidenceDisplayGroup,
  type EvidenceReferenceIndex,
} from "../../evidence";

export function EvidencePanel({
  evidence,
  evidenceStatus,
  runStatus,
  focusedRef,
  onReturn,
  returnLabel,
  evidenceIndex,
  onEvidence,
}: {
  evidence: EvidenceBundle | null;
  evidenceStatus: RunDetailType["evidence_status"]["status"];
  runStatus: RunDetailType["run"]["status"];
  focusedRef: string;
  onReturn: () => void;
  returnLabel: string;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <article
      className="panel audit-panel"
      id="run-view-evidence"
      role="tabpanel"
    >
      <div className="panel-header">
        <div>
          <p className="eyebrow">{t("evidenceBundle")}</p>
          <h2>{t("evidence")}</h2>
        </div>
        <div className="evidence-panel-actions">
          <button
            type="button"
            className="button compact-button"
            onClick={onReturn}
          >
            ← {returnLabel}
          </button>
          <span className="event-count">{evidenceIndex.groups.length}</span>
        </div>
      </div>
      {!evidence ? (
        <div className="empty-state">
          {evidenceStatus === "pending" &&
          (runStatus === "queued" || runStatus === "running")
            ? t("evidencePending")
            : t("noEvidenceRecorded")}
        </div>
      ) : (
        <>
          <dl className="bundle-summary">
            <div>
              <dt>{t("evidenceDigest")}</dt>
              <dd>{evidence.digest ?? "—"}</dd>
            </div>
            <div>
              <dt>{t("analysisDate")}</dt>
              <dd>{evidence.analysis_date}</dd>
            </div>
            <div>
              <dt>{t("version")}</dt>
              <dd>{evidence.version ?? "1"}</dd>
            </div>
            <div>
              <dt>{t("displayedEvidence")}</dt>
              <dd>
                {t("evidenceBodiesSummary", {
                  groups: evidenceIndex.groups.length,
                  items: evidence.items.length,
                })}
              </dd>
            </div>
          </dl>
          <div className="evidence-list">
            {evidenceIndex.groups.map((group) => (
              <EvidenceCard
                group={group}
                focused={group.refs.includes(focusedRef)}
                key={group.alias}
              />
            ))}
          </div>
          {(evidence.tables ?? []).length > 0 && (
            <section className="evidence-table-list">
              <h3>{t("rawEvidenceTables")}</h3>
              {(evidence.tables ?? []).map((table) => (
                <EvidenceTableView
                  table={table}
                  evidenceIndex={evidenceIndex}
                  onEvidence={onEvidence}
                  key={table.id}
                />
              ))}
            </section>
          )}
        </>
      )}
    </article>
  );
}

function EvidenceCard({
  group,
  focused,
}: {
  group: EvidenceDisplayGroup;
  focused: boolean;
}) {
  const { t } = useTranslation();
  const item = group.canonical;
  const hasValue = item.value !== null && item.value !== undefined;
  const requestedDates = uniqueStrings(
    group.items.map((entry) => entry.requested_date),
  );
  const effectiveDates = uniqueStrings(
    group.items.flatMap((entry) =>
      entry.effective_date ? [entry.effective_date] : [],
    ),
  );
  const availableDates = uniqueStrings(
    group.items.flatMap((entry) =>
      entry.available_at ? [entry.available_at] : [],
    ),
  );
  const auditRecords = group.items.map(
    ({ content: _content, ...entry }) => entry,
  );
  return (
    <section
      className={`evidence-card ${focused ? "focused" : ""}`}
      id={`evidence-${item.ref}`}
      data-evidence-ref={group.refs.join(" ")}
      tabIndex={-1}
    >
      <header>
        <div>
          <code title={group.refs.join("\n")}>{group.alias}</code>
          <h3>{group.evidenceTypes.join(" · ")}</h3>
        </div>
        <span className={`quality quality-${group.quality}`}>
          {group.quality}
        </span>
      </header>
      <dl className="evidence-metadata">
        <div>
          <dt>{t("source")}</dt>
          <dd>{group.sources.join(", ")}</dd>
        </div>
        <div>
          <dt>{t("requestedDate")}</dt>
          <dd>{requestedDates.join(", ")}</dd>
        </div>
        <div>
          <dt>{t("effectiveDate")}</dt>
          <dd>{effectiveDates.join(", ") || "—"}</dd>
        </div>
        <div>
          <dt>{t("availableAt")}</dt>
          <dd>{availableDates.join(", ") || "—"}</dd>
        </div>
        <div>
          <dt>{t("fallback")}</dt>
          <dd>{group.fallback ? t("yes") : t("no")}</dd>
        </div>
        {hasValue && (
          <div>
            <dt>{t("value")}</dt>
            <dd>
              {String(item.value)} {item.unit ?? ""}
            </dd>
          </div>
        )}
      </dl>
      {item.content && (
        <div className="evidence-content">
          <Markdown>{item.content}</Markdown>
        </div>
      )}
      <details className="provenance-details evidence-audit-details">
        <summary>{t("canonicalEvidenceAndProvenance")}</summary>
        <div className="canonical-ref-list">
          {group.refs.map((ref) => (
            <div key={ref}>
              <code>{ref}</code>
              <button
                type="button"
                className="copy-ref-button"
                title={ref}
                aria-label={t("copyEvidenceId", { ref })}
                onClick={() => void copyEvidenceRef(ref)}
              >
                {t("copy")}
              </button>
            </div>
          ))}
        </div>
        <pre>{JSON.stringify(auditRecords, null, 2)}</pre>
      </details>
    </section>
  );
}

export function EvidenceSourceDrawer({
  evidenceRef,
  evidenceIndex,
  onClose,
}: {
  evidenceRef: string | null;
  evidenceIndex: EvidenceReferenceIndex;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const group = evidenceRef
    ? evidenceIndex.groups.find((candidate) =>
        candidate.refs.includes(evidenceRef),
      )
    : undefined;

  useEffect(() => {
    if (!evidenceRef) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [evidenceRef, onClose]);

  if (!evidenceRef) return null;
  return (
    <div className="source-drawer-layer" role="presentation">
      <button
        type="button"
        className="source-drawer-backdrop"
        aria-label={t("closeSourceDetails")}
        onClick={onClose}
      />
      <aside
        className="source-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={t("sourceDetails")}
      >
        <header>
          <div>
            <p className="eyebrow">{t("sourceDetails")}</p>
            <h2>{group?.sources.join(", ") || t("unknownSource")}</h2>
          </div>
          <button type="button" className="button" onClick={onClose}>
            {t("close")}
          </button>
        </header>
        {!group ? (
          <div className="empty-state">{t("evidenceReferenceUnavailable")}</div>
        ) : (
          <>
            <dl className="evidence-metadata">
              <div>
                <dt>{t("quality")}</dt>
                <dd>{group.quality}</dd>
              </div>
              <div>
                <dt>{t("effectiveDate")}</dt>
                <dd>{evidenceDates(group).join(", ") || "—"}</dd>
              </div>
              <div>
                <dt>{t("fallback")}</dt>
                <dd>{group.fallback ? t("yes") : t("no")}</dd>
              </div>
            </dl>
            {group.canonical.content && (
              <div className="source-drawer-content">
                <Markdown>{group.canonical.content}</Markdown>
              </div>
            )}
            {group.canonical.value !== null &&
              group.canonical.value !== undefined && (
                <p className="source-drawer-value">
                  <strong>{t("value")}:</strong>{" "}
                  {String(group.canonical.value)}{" "}
                  {group.canonical.unit ?? ""}
                </p>
              )}
            <details className="provenance-details">
              <summary>{t("canonicalEvidenceAndProvenance")}</summary>
              <div className="canonical-ref-list">
                {group.refs.map((ref) => (
                  <div key={ref}>
                    <code>{ref}</code>
                    <button
                      type="button"
                      className="copy-ref-button"
                      onClick={() => void copyEvidenceRef(ref)}
                    >
                      {t("copy")}
                    </button>
                  </div>
                ))}
              </div>
              <pre>
                {JSON.stringify(
                  group.items.map(({ content: _content, ...item }) => item),
                  null,
                  2,
                )}
              </pre>
            </details>
          </>
        )}
      </aside>
    </div>
  );
}

function evidenceDates(group: EvidenceDisplayGroup): string[] {
  return Array.from(
    new Set(
      group.items.flatMap((item) =>
        item.effective_date
          ? [item.effective_date]
          : item.requested_date
            ? [item.requested_date]
            : [],
      ),
    ),
  );
}

function uniqueStrings(values: string[]): string[] {
  return Array.from(new Set(values));
}

async function copyEvidenceRef(ref: string) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(ref);
    }
  } catch {
    // Clipboard permission failures do not affect evidence navigation.
  }
}
