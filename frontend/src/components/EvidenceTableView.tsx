import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type { EvidenceTable } from "../api/client";
import type { EvidenceReferenceIndex } from "../evidence";

const pageSize = 50;

export default function EvidenceTableView({
  table,
  evidenceIndex,
  onEvidence,
}: {
  table: EvidenceTable;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
}) {
  const { t } = useTranslation();
  const [page, setPage] = useState(0);
  const pageCount = Math.max(1, Math.ceil(table.rows.length / pageSize));
  const rows = useMemo(
    () => table.rows.slice(page * pageSize, (page + 1) * pageSize),
    [page, table.rows],
  );

  return (
    <section className="research-table-card evidence-table-card">
      <header className="research-table-header">
        <div>
          <span className="research-table-kind">{t("evidenceDataTable")}</span>
          <h4>{table.title}</h4>
          <p>{table.purpose}</p>
        </div>
        <small>{table.source_format}</small>
      </header>
      <div className="research-table-scroll">
        <table>
          <thead>
            <tr>
              {table.columns.map((column) => (
                <th key={column.key}>
                  {column.label}
                  {column.unit && <small>{column.unit}</small>}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id}>
                {table.columns.map((column) => (
                  <td key={column.key}>
                    {displayRawValue(row.cells[column.key]?.raw_value)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <footer className="research-table-controls">
        <span>
          {t("sourceRowsDisplayed", {
            displayed: rows.length,
            total: table.rows.length,
          })}
        </span>
        {pageCount > 1 && (
          <div>
            <button
              type="button"
              disabled={page === 0}
              onClick={() => setPage((value) => value - 1)}
            >
              ←
            </button>
            <span>
              {page + 1}/{pageCount}
            </span>
            <button
              type="button"
              disabled={page + 1 >= pageCount}
              onClick={() => setPage((value) => value + 1)}
            >
              →
            </button>
          </div>
        )}
        {table.evidence_refs.length > 0 && (
          <button
            type="button"
            className="open-evidence-button"
            onClick={() =>
              onEvidence(
                evidenceIndex.primaryRefs[table.evidence_refs[0]] ??
                  table.evidence_refs[0],
              )
            }
          >
            {t("openEvidence")}
          </button>
        )}
      </footer>
    </section>
  );
}

function displayRawValue(value: string | number | boolean | null | undefined) {
  if (value === null || value === undefined) return "—";
  if (typeof value === "boolean") return value ? "true" : "false";
  return String(value);
}
