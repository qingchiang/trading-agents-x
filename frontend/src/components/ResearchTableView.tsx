import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  EvidenceTable,
  ResearchTable,
  ResearchTableCell,
  ResearchTableRow,
} from "../api/client";
import type { EvidenceReferenceIndex } from "../evidence";
import EvidenceLinks from "./EvidenceLinks";

const visualPageSize = 12;

export default function ResearchTableView({
  table,
  evidenceIndex,
  onEvidence,
  sourceTable,
  nested = false,
}: {
  table: EvidenceTable | ResearchTable;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
  sourceTable?: EvidenceTable;
  nested?: boolean;
}) {
  const { t } = useTranslation();
  const [page, setPage] = useState(0);
  const [showAll, setShowAll] = useState(false);
  const [sourceOpen, setSourceOpen] = useState(false);
  const evidenceTable = isEvidenceTable(table);
  const totalPages = Math.max(1, Math.ceil(table.rows.length / visualPageSize));
  const safePage = Math.min(page, totalPages - 1);
  const visibleRows = showAll
    ? table.rows
    : table.rows.slice(
        safePage * visualPageSize,
        (safePage + 1) * visualPageSize,
      );
  const startRow = showAll ? 1 : safePage * visualPageSize + 1;
  const endRow = showAll
    ? table.rows.length
    : Math.min((safePage + 1) * visualPageSize, table.rows.length);
  const titleRefs = useMemo(
    () => uniqueRefs(table.evidence_refs ?? []),
    [table.evidence_refs],
  );
  const hasRowOverrides = table.rows.some(
    (row) => (row.evidence_refs?.length ?? 0) > 0,
  );

  useEffect(() => {
    if (page >= totalPages) setPage(totalPages - 1);
  }, [page, totalPages]);

  return (
    <section
      className={[
        "research-table-card",
        evidenceTable ? "evidence-table-card" : "analysis-table-card",
        nested ? "nested" : "",
      ]
        .filter(Boolean)
        .join(" ")}
      data-table-id={table.id}
    >
      <header className="research-table-header">
        <div>
          <span className="research-table-kind">
            {t(evidenceTable ? "evidenceDataTable" : "aiResearchTable")}
          </span>
          <h4>{table.title}</h4>
          <p>{table.purpose}</p>
        </div>
        <EvidenceLinks
          refs={titleRefs}
          evidenceIndex={evidenceIndex}
          onEvidence={onEvidence}
          label={false}
          compact
          className="table-title-evidence"
        />
      </header>

      {!evidenceTable && table.source_evidence_table_id && (
        <div className="source-view-notice">
          <span>
            {t("sourceRowsDisplayed", {
              displayed: table.rows.length,
              total: table.total_source_rows ?? table.rows.length,
            })}
          </span>
          {sourceTable && !nested && (
            <button
              type="button"
              className="text-button"
              onClick={() => setSourceOpen((current) => !current)}
              aria-expanded={sourceOpen}
            >
              {t(
                sourceOpen
                  ? "closeFullEvidenceTable"
                  : "openFullEvidenceTable",
              )}
            </button>
          )}
        </div>
      )}

      <div className="research-table-scroll">
        <table>
          <thead>
            <tr>
              {hasRowOverrides && (
                <th className="table-row-audit">{t("rowEvidence")}</th>
              )}
              {table.columns.map((column) => (
                <th key={column.key}>
                  <span>{column.label}</span>
                  {(column.display?.unit_label ?? column.unit) && (
                    <small>
                      {column.display?.unit_label ?? column.unit}
                    </small>
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visibleRows.map((row) => (
              <ResearchRow
                columns={table.columns}
                row={row}
                evidenceIndex={evidenceIndex}
                onEvidence={onEvidence}
                tableRefs={titleRefs}
                showRowAudit={hasRowOverrides}
                key={row.id}
              />
            ))}
          </tbody>
        </table>
      </div>

      {table.rows.length > visualPageSize && (
        <footer className="research-table-controls">
          <span>
            {t("tableRowsDisplayed", {
              start: startRow,
              end: endRow,
              total: table.rows.length,
            })}
          </span>
          <div>
            {!showAll && (
              <>
                <button
                  type="button"
                  className="text-button"
                  disabled={safePage === 0}
                  onClick={() => setPage((current) => Math.max(0, current - 1))}
                >
                  {t("previousRows")}
                </button>
                <button
                  type="button"
                  className="text-button"
                  disabled={safePage >= totalPages - 1}
                  onClick={() =>
                    setPage((current) =>
                      Math.min(totalPages - 1, current + 1),
                    )
                  }
                >
                  {t("nextRows")}
                </button>
              </>
            )}
            <button
              type="button"
              className="text-button"
              onClick={() => {
                setShowAll((current) => !current);
                setPage(0);
              }}
            >
              {t(showAll ? "showPagedRows" : "showAllRows")}
            </button>
          </div>
        </footer>
      )}

      {sourceOpen && sourceTable && !nested && (
        <div className="full-source-table">
          <div className="full-source-table-heading">
            <strong>{t("completeEvidenceTable")}</strong>
            <button
              type="button"
              className="text-button"
              onClick={() => setSourceOpen(false)}
            >
              {t("close")}
            </button>
          </div>
          <ResearchTableView
            table={sourceTable}
            evidenceIndex={evidenceIndex}
            onEvidence={onEvidence}
            nested
          />
        </div>
      )}
    </section>
  );
}

function ResearchRow({
  columns,
  row,
  evidenceIndex,
  onEvidence,
  tableRefs,
  showRowAudit,
}: {
  columns: EvidenceTable["columns"];
  row: ResearchTableRow;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
  tableRefs: string[];
  showRowAudit: boolean;
}) {
  const rowRefs = uniqueRefs(row.evidence_refs ?? []);
  const inheritedRefs = rowRefs.length > 0 ? rowRefs : tableRefs;
  return (
    <tr data-row-id={row.id}>
      {showRowAudit && (
        <th className="table-row-audit" scope="row" title={row.id}>
          <EvidenceLinks
            refs={rowRefs}
            evidenceIndex={evidenceIndex}
            onEvidence={onEvidence}
            label={false}
            compact
          />
        </th>
      )}
      {columns.map((column) => (
        <ResearchCell
          cell={row.cells[column.key]}
          evidenceIndex={evidenceIndex}
          onEvidence={onEvidence}
          inheritedRefs={inheritedRefs}
          key={column.key}
        />
      ))}
    </tr>
  );
}

function ResearchCell({
  cell,
  evidenceIndex,
  onEvidence,
  inheritedRefs,
}: {
  cell: ResearchTableCell;
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
  inheritedRefs: string[];
}) {
  const { t } = useTranslation();
  const explicitRefs = uniqueRefs(cell.evidence_refs ?? []);
  const visibleRefs =
    cell.derived || !sameRefs(explicitRefs, inheritedRefs)
      ? explicitRefs
      : [];
  return (
    <td className={`table-cell-${cell.kind ?? "observation"}`}>
      <span className="table-cell-value">{cell.display_value}</span>
      <EvidenceLinks
        refs={visibleRefs}
        evidenceIndex={evidenceIndex}
        onEvidence={onEvidence}
        label={false}
        compact
        className="table-cell-evidence"
      />
      {cell.derived && (
        <details className="derived-value-details">
          <summary>{t("derivedValue")}</summary>
          <dl>
            <div>
              <dt>{t("formula")}</dt>
              <dd>
                <code>{cell.derived.formula}</code>
              </dd>
            </div>
            <div>
              <dt>{t("inputs")}</dt>
              <dd>
                <code>{JSON.stringify(cell.derived.inputs)}</code>
              </dd>
            </div>
          </dl>
        </details>
      )}
    </td>
  );
}

function isEvidenceTable(
  table: EvidenceTable | ResearchTable,
): table is EvidenceTable {
  return "evidence_refs" in table && "source_format" in table;
}

function uniqueRefs(refs: string[]): string[] {
  return Array.from(new Set(refs));
}

function sameRefs(left: string[], right: string[]): boolean {
  if (left.length !== right.length) return false;
  const rightSet = new Set(right);
  return left.every((ref) => rightSet.has(ref));
}
