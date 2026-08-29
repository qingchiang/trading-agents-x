import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";

import {
  api,
  type ResearchNodeComparison,
  type ResearchNodeComparisonSelection,
  type ResearchNodeView,
  type ResearchTimelinePage,
  type TimelineDetail,
} from "../api/client";
import ConfirmDialog from "../components/ConfirmDialog";
import { InstrumentIdentity } from "../components/Instruments";
import ResearchRatingBadge from "../components/ResearchRatingBadge";
import { Link, usePathname } from "../router";

const CYCLE_PAGE_SIZE = 12;

function KindBadge({ kind }: { kind: ResearchNodeView["research_kind"] }) {
  const { t } = useTranslation();
  return (
    <span className={`research-kind-badge ${kind}`}>
      {t(kind === "full" ? "fullResearch" : "incrementalResearch")}
    </span>
  );
}

function Confidence({ value }: { value?: number | null }) {
  const { t } = useTranslation();
  return (
    <span className="confidence-value">
      {value == null
        ? t("notRecorded")
        : t("confidencePercent", { value: Math.round(value * 100) })}
    </span>
  );
}

function DecisionSummary({ node }: { node: ResearchNodeView }) {
  const { t } = useTranslation();
  if (!node.decision) return <p className="muted-copy">{t("notRecorded")}</p>;
  return (
    <section className="decision-summary" aria-label={t("currentDecision")}>
      <div className="decision-summary-meta">
        <ResearchRatingBadge rating={node.decision.rating} />
        <Confidence value={node.decision.confidence} />
      </div>
      <p>{node.decision.thesis}</p>
    </section>
  );
}

function IncrementalProducts({ node }: { node: ResearchNodeView }) {
  const { t } = useTranslation();
  const collectionDomains = node.collection_summary?.domains ?? [];
  const availabilityDomains = node.research_availability?.domains ?? [];
  const reassessmentEntries = node.reassessment?.entries ?? [];
  const changedEntries = reassessmentEntries.filter(
    (entry) => entry.disposition !== "reaffirmed",
  );
  return (
    <div className="incremental-products">
      <div className="incremental-summary-strip">
        <section>
          <h4>{t("advancementType")}</h4>
          <p>
            {advancementSummary(
              t,
              node.information_advancement?.reasons ?? [],
            )}
          </p>
        </section>
        {node.performance && (
          <section>
            <h4>{t("performanceSummary")}</h4>
            <PerformanceSummary node={node} />
          </section>
        )}
        {node.reassessment && (
          <section>
            <h4>{t("reassessmentChanges")}</h4>
            <p>
              {t("nonReaffirmedCount", { count: changedEntries.length })}
            </p>
          </section>
        )}
      </div>

      <details className="incremental-update-details">
        <summary>{t("updateDetails")}</summary>
        <div className="incremental-update-detail-grid">
          <section>
            <h4>{t("researchAvailability")}</h4>
            <div className="availability-row">
              {availabilityDomains.length > 0 ? (
                availabilityDomains.map((domain) => (
                  <span
                    className={`availability-chip ${domain.status}`}
                    key={domain.domain}
                  >
                    {t(`${domain.domain}Analyst`)} ·{" "}
                    {t(`availability_${domain.status}`)}
                  </span>
                ))
              ) : (
                <span className="muted-copy">{t("notRecorded")}</span>
              )}
            </div>
          </section>
          <section>
            <h4>{t("collectionSummary")}</h4>
            {collectionDomains.length > 0 ? (
              <ul className="compact-list collection-summary-list">
                {collectionDomains.map((domain) => (
                  <li key={domain.domain}>
                    <div className="collection-domain-heading">
                      <strong>{t(`${domain.domain}Analyst`)}</strong>
                      <span className={`availability-chip ${domain.state}`}>
                        {t(`collection_${domain.state}`)}
                      </span>
                    </div>
                    {((domain.sources?.length ?? 0) > 0 ||
                      domain.diagnostic) && (
                      <details className="audit-disclosure">
                        <summary>{t("auditDetails")}</summary>
                        {(domain.sources?.length ?? 0) > 0 && (
                          <ul className="collection-source-list">
                            {domain.sources?.map((source, index) => (
                              <li
                                key={`${source.source}-${source.retrieved_at}-${index}`}
                              >
                                <span>{source.source}</span>
                                {source.fallback && (
                                  <span className="muted-copy">
                                    {" "}· {t("fallback")}
                                  </span>
                                )}
                                {source.diagnostic && (
                                  <span className="muted-copy">
                                    {" "}· {source.diagnostic.code}
                                  </span>
                                )}
                              </li>
                            ))}
                          </ul>
                        )}
                        {domain.diagnostic && (
                          <p className="muted-copy">
                            {domain.diagnostic.code}
                          </p>
                        )}
                      </details>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted-copy">{t("notRecorded")}</p>
            )}
          </section>
          <section>
            <h4>{t("reassessment")}</h4>
            {reassessmentEntries.length > 0 ? (
              <ul className="compact-list timeline-reassessment-list">
                {reassessmentEntries.map((entry) => (
                  <li key={entry.component_id}>
                    <strong>{t(`reassessment_${entry.disposition}`)}</strong>
                    <p>{entry.reason}</p>
                    <details className="audit-disclosure">
                      <summary>{t("auditDetails")}</summary>
                      <code>{entry.component_id}</code>
                    </details>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted-copy">{t("notRecorded")}</p>
            )}
          </section>
        </div>
      </details>

      {(node.full_research_required_reasons?.length ?? 0) > 0 && (
        <section className="research-warning-block" role="status">
          <h4>{t("fullResearchRecommended")}</h4>
          {node.full_research_required_reasons?.map((reason) => (
            <p key={reason.code}>{reason.message}</p>
          ))}
        </section>
      )}
    </div>
  );
}

function PerformanceSummary({ node }: { node: ResearchNodeView }) {
  const { t } = useTranslation();
  const performance = node.performance;
  if (!performance) return null;
  return (
    <div className="timeline-performance-summary">
      <span>{performanceComponentText(t, t("stockReturn"), performance.stock)}</span>
      {(performance.benchmarks ?? []).map((benchmark) => (
        <span key={benchmark.name}>
          {performanceComponentText(t, benchmark.name, benchmark.component)}
          {benchmark.reported_difference != null && (
            <> · {t("reportedBenchmarkDifference")}: {formatPercent(benchmark.reported_difference)}</>
          )}
        </span>
      ))}
    </div>
  );
}

function NodeCard({
  node,
  selected,
  comparisonFull,
  onToggleComparison,
  onLifecycle,
  onReload,
}: {
  node: ResearchNodeView;
  selected: boolean;
  comparisonFull: boolean;
  onToggleComparison: (node: ResearchNodeView) => void;
  onLifecycle: (node: ResearchNodeView, mode: "trash" | "purge") => void;
  onReload: () => Promise<void>;
}) {
  const { t } = useTranslation();
  return (
    <article className={`research-node-card ${node.research_kind} ${node.is_active ? "" : "trashed"}`}>
      <header className="research-node-header">
        <div>
          <KindBadge kind={node.research_kind} />
          <h3>{node.analysis_date}</h3>
        </div>
        <div className="status-cluster">
          {node.is_cycle_head && <span className="status-pill">{t("cycleHead")}</span>}
          {!node.is_active && <span className="status-pill muted">{t("retainedInTrash")}</span>}
        </div>
      </header>
      <DecisionSummary node={node} />
      {node.research_kind === "incremental" && <IncrementalProducts node={node} />}
      <details className="audit-disclosure">
        <summary>{t("auditDetails")}</summary>
        <dl className="definition-list compact-definition-list">
          <div><dt>{t("informationCutoff")}</dt><dd>{new Date(node.information_cutoff_at).toLocaleString()}</dd></div>
          <div><dt>{t("researchSchema")}</dt><dd>{node.research_schema_version}</dd></div>
          <div><dt>{t("methodProvider")}</dt><dd>{String(node.method_snapshot.llm_provider ?? t("notRecorded"))}</dd></div>
          <div><dt>{t("runId")}</dt><dd><code>{node.id}</code></dd></div>
        </dl>
        <details>
          <summary>{t("methodSnapshot")}</summary>
          <pre>{JSON.stringify(node.method_snapshot, null, 2)}</pre>
        </details>
      </details>
      <footer className="node-actions">
        <button
          type="button"
          className={`button compact-button ${selected ? "selected" : ""}`}
          disabled={comparisonFull && !selected}
          aria-pressed={selected}
          onClick={() => onToggleComparison(node)}
        >
          {t(selected ? "removeFromComparison" : "selectForComparison")}
        </button>
        {node.is_active ? (
          <button
            type="button"
            className="button compact-button danger"
            onClick={() => onLifecycle(node, "trash")}
          >
            {t(node.research_kind === "full" ? "moveCycleToTrash" : "moveNodeToTrash")}
          </button>
        ) : (
          <>
            <button
              type="button"
              className="button compact-button"
              onClick={() => void api.restoreRuns([node.id]).then(onReload)}
            >
              {t("restoreResearchNode")}
            </button>
            <button
              type="button"
              className="button compact-button danger"
              onClick={() => onLifecycle(node, "purge")}
            >
              {t("purgeResearchNode")}
            </button>
          </>
        )}
        <Link className="text-link" to={`/runs/${encodeURIComponent(node.id)}`}>
          {t("openResearchDetail")} →
        </Link>
      </footer>
    </article>
  );
}

const DECISION_FIELD_LABELS: Record<string, string> = {
  rating: "researchRating",
  confidence: "confidence",
  executive_summary: "executiveSummary",
  thesis: "thesis",
  evidence_refs: "evidenceRefs",
  catalysts: "catalysts",
  risks: "risks",
  invalidation_conditions: "invalidation",
  unresolved_questions: "unresolvedQuestions",
  time_horizon: "horizon",
  scenarios: "scenarios",
  valuation_assessment: "valuationAssessment",
  market_reference_levels: "marketReferenceLevels",
  calculation_records: "calculationRecords",
  risk_review_adjustments: "riskReviewAdjustments",
  numeric_audit_status: "numericAuditStatus",
};

const STRUCTURED_VALUE_LABELS: Record<string, string> = {
  as_of_date: "asOfDate",
  core_assumptions: "coreAssumptions",
  evidence_refs: "evidenceRefs",
  kind: "scenario",
  limitations: "limitations",
  outcome: "scenarioOutcome",
  reference_ranges: "scenarioReferenceRange",
};

function comparisonFieldLabel(t: TFunction, key: string) {
  const translationKey = DECISION_FIELD_LABELS[key] ?? STRUCTURED_VALUE_LABELS[key];
  return translationKey ? t(translationKey) : key;
}

function StructuredComparisonValue({ value }: { value: unknown }) {
  const { t } = useTranslation();
  if (value === null || value === undefined) {
    return <span className="muted-copy">{t("notApplicable")}</span>;
  }
  if (typeof value === "boolean") return <span>{t(value ? "yes" : "no")}</span>;
  if (typeof value === "string" || typeof value === "number") return <span>{value}</span>;
  if (Array.isArray(value)) {
    return (
      <ul className="comparison-value-list">
        {value.map((item, index) => (
          <li key={index}><StructuredComparisonValue value={item} /></li>
        ))}
      </ul>
    );
  }
  if (typeof value === "object") {
    return (
      <dl className="comparison-value-fields">
        {Object.entries(value).map(([key, nestedValue]) => (
          <div key={key}>
            <dt>{comparisonFieldLabel(t, key)}</dt>
            <dd><StructuredComparisonValue value={nestedValue} /></dd>
          </div>
        ))}
      </dl>
    );
  }
  return <span>{String(value)}</span>;
}

function ComparisonValue({ value }: { value: unknown }) {
  const { t } = useTranslation();
  if (value === null || value === undefined || value === "") {
    return <span className="muted-copy">{t("notApplicable")}</span>;
  }
  return <StructuredComparisonValue value={value} />;
}

function DecisionComparisonValue({
  comparisonValue,
  sectionKey,
}: {
  comparisonValue: ResearchNodeComparison["decision_sections"][number]["values"][number] | undefined;
  sectionKey?: string;
}) {
  const { t } = useTranslation();
  if (!comparisonValue || comparisonValue.state === "not_recorded_under_this_schema") {
    return <span className="muted-copy">{t("notRecordedUnderThisSchema")}</span>;
  }
  if (comparisonValue.state === "null") {
    return <span className="muted-copy">{t("comparisonNull")}</span>;
  }
  if (comparisonValue.state === "empty") {
    return <span className="muted-copy">{t("comparisonEmpty")}</span>;
  }
  if (
    sectionKey === "confidence" &&
    typeof comparisonValue.value === "number"
  ) {
    return <Confidence value={comparisonValue.value} />;
  }
  return <StructuredComparisonValue value={comparisonValue.value} />;
}

const CORE_DECISION_FIELDS = new Set([
  "rating",
  "confidence",
  "executive_summary",
  "thesis",
  "catalysts",
  "risks",
  "invalidation_conditions",
]);
const RAW_DECISION_FIELDS = new Set([
  "evidence_refs",
  "calculation_records",
  "numeric_audit_status",
]);

type ComparisonSide = ResearchNodeComparison["sides"][number];
type ComparisonSection = ResearchNodeComparison["decision_sections"][number];
type ProductComparisonRow = {
  key: string;
  label: string;
  values: [unknown, unknown];
};

function NodeComparisonModal({
  comparison,
  onClose,
}: {
  comparison: ResearchNodeComparison;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const titleId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const [swapped, setSwapped] = useState(false);
  const [changedOnly, setChangedOnly] = useState(true);
  const sideIndexes: [number, number] = swapped ? [1, 0] : [0, 1];
  const sides = sideIndexes.map((index) => comparison.sides[index]) as [
    ComparisonSide,
    ComparisonSide,
  ];
  const coreSections = filterDecisionSections(
    comparison.decision_sections.filter((section) =>
      CORE_DECISION_FIELDS.has(section.key),
    ),
    changedOnly,
  );
  const extendedSections = filterDecisionSections(
    comparison.decision_sections.filter(
      (section) =>
        !CORE_DECISION_FIELDS.has(section.key) &&
        !RAW_DECISION_FIELDS.has(section.key),
    ),
    changedOnly,
  );
  const rawSections = filterDecisionSections(
    comparison.decision_sections.filter((section) =>
      RAW_DECISION_FIELDS.has(section.key),
    ),
    changedOnly,
  );
  const primaryProducts = filterProductRows(
    [
      productRow(
        "performance",
        t("performance"),
        comparison.sides,
        (side) => performanceComparisonText(t, side),
      ),
      productRow(
        "full-research-required",
        t("fullResearchRecommended"),
        comparison.sides,
        (side) =>
          side.full_research_required_reasons
            ?.map((reason) => reason.message)
            .join("\n") || null,
      ),
    ],
    changedOnly,
  );
  const updateProducts = filterProductRows(
    [
      productRow(
        "advancement",
        t("informationAdvancement"),
        comparison.sides,
        (side) =>
          side.information_advancement
            ? advancementSummary(
                t,
                side.information_advancement.reasons ?? [],
              )
            : null,
      ),
      productRow(
        "availability",
        t("researchAvailability"),
        comparison.sides,
        (side) => availabilityComparisonText(t, side),
      ),
      productRow(
        "reassessment",
        t("reassessment"),
        comparison.sides,
        (side) => reassessmentComparisonText(t, side),
      ),
      productRow("method", t("method"), comparison.sides, methodSummary),
    ],
    changedOnly,
  );

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = focusableElements(dialogRef.current);
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      previouslyFocused?.focus();
    };
  }, [onClose]);

  return (
    <div
      className="comparison-modal-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className="comparison-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        ref={dialogRef}
      >
        <header className="comparison-modal-header">
          <div>
            <p className="eyebrow">{t("selectedResearchNodes")}</p>
            <h2 id={titleId}>{t("nodeComparison")}</h2>
            <span>
              {t(
                comparison.cross_cycle
                  ? "crossCycleComparison"
                  : "sameCycleComparison",
              )}
            </span>
          </div>
          <div className="comparison-modal-actions">
            <button
              type="button"
              className="button"
              onClick={() => setSwapped((value) => !value)}
            >
              {t("swapComparisonSides")}
            </button>
            <button
              ref={closeRef}
              type="button"
              className="button"
              onClick={onClose}
            >
              {t("close")}
            </button>
          </div>
        </header>

        <div className="comparison-modal-controls">
          <label>
            <input
              type="checkbox"
              checked={changedOnly}
              onChange={(event) => setChangedOnly(event.target.checked)}
            />
            {t("showChangedOnly")}
          </label>
        </div>

        <div className="comparison-modal-scroll">
          {comparison.method_changed && (
            <div className="notice" role="status">
              {t("methodChanged")}
            </div>
          )}
          {(comparison.warnings?.length ?? 0) > 0 && (
            <div className="comparison-warning-list">
              {comparison.warnings?.map((warning) => (
                <p key={warning.code}>{warning.message}</p>
              ))}
            </div>
          )}

          <ComparisonTable
            sections={coreSections}
            productRows={primaryProducts}
            sides={sides}
            sideIndexes={sideIndexes}
          />

          <ComparisonDisclosure title={t("extendedConclusions")}>
            <ComparisonSectionList
              sections={extendedSections}
              sides={sides}
              sideIndexes={sideIndexes}
            />
          </ComparisonDisclosure>

          <ComparisonDisclosure title={t("updateAudit")}>
            <ComparisonProductList
              rows={updateProducts}
              sides={sides}
              sideIndexes={sideIndexes}
            />
          </ComparisonDisclosure>

          <ComparisonDisclosure title={t("rawAudit")} audit>
            <ComparisonSectionList
              sections={rawSections}
              sides={sides}
              sideIndexes={sideIndexes}
            />
            <section className="comparison-raw-sides">
              {sides.map((side) => (
                <div key={side.node_id}>
                  <h3>{side.analysis_date}</h3>
                  <dl className="definition-list compact-definition-list">
                    <div>
                      <dt>{t("researchSchema")}</dt>
                      <dd>{side.research_schema_version}</dd>
                    </div>
                  </dl>
                  <pre>{JSON.stringify(side, null, 2)}</pre>
                </div>
              ))}
            </section>
          </ComparisonDisclosure>
        </div>
      </div>
    </div>
  );
}

function ComparisonTable({
  sections,
  productRows,
  sides,
  sideIndexes,
}: {
  sections: ComparisonSection[];
  productRows: ProductComparisonRow[];
  sides: [ComparisonSide, ComparisonSide];
  sideIndexes: [number, number];
}) {
  const { t } = useTranslation();
  return (
    <div className="table-wrap comparison-decision-table">
      <table>
        <thead>
          <tr>
            <th>{t("decisionSection")}</th>
            {sides.map((side) => (
              <th key={side.node_id}>
                <KindBadge kind={side.research_kind} />
                <span>{side.analysis_date}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sections.map((section) => (
            <tr key={section.key}>
              <th scope="row">{comparisonFieldLabel(t, section.key)}</th>
              {sideIndexes.map((sideIndex) => (
                <td key={sideIndex}>
                  <DecisionComparisonValue
                    comparisonValue={section.values[sideIndex]}
                    sectionKey={section.key}
                  />
                </td>
              ))}
            </tr>
          ))}
          {productRows.map((row) => (
            <tr key={row.key}>
              <th scope="row">{row.label}</th>
              {sideIndexes.map((sideIndex) => (
                <td key={sideIndex}>
                  <ComparisonValue value={row.values[sideIndex]} />
                </td>
              ))}
            </tr>
          ))}
          {sections.length === 0 && productRows.length === 0 && (
            <tr>
              <td colSpan={3} className="muted-copy">
                {t("comparisonNoChangedSections")}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function ComparisonDisclosure({
  title,
  audit = false,
  children,
}: {
  title: string;
  audit?: boolean;
  children: ReactNode;
}) {
  return (
    <details className={`comparison-disclosure ${audit ? "audit" : ""}`}>
      <summary>{title}</summary>
      <div className="comparison-disclosure-body">{children}</div>
    </details>
  );
}

function ComparisonSectionList({
  sections,
  sides,
  sideIndexes,
}: {
  sections: ComparisonSection[];
  sides: [ComparisonSide, ComparisonSide];
  sideIndexes: [number, number];
}) {
  const { t } = useTranslation();
  if (sections.length === 0) {
    return <p className="muted-copy">{t("comparisonNoChangedSections")}</p>;
  }
  return (
    <div className="comparison-section-list">
      {sections.map((section) => (
        <section key={section.key}>
          <h3>{comparisonFieldLabel(t, section.key)}</h3>
          <div className="comparison-side-by-side">
            {sideIndexes.map((sideIndex, position) => (
              <div key={sideIndex}>
                <strong>{sides[position].analysis_date}</strong>
                <DecisionComparisonValue
                  comparisonValue={section.values[sideIndex]}
                  sectionKey={section.key}
                />
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function ComparisonProductList({
  rows,
  sides,
  sideIndexes,
}: {
  rows: ProductComparisonRow[];
  sides: [ComparisonSide, ComparisonSide];
  sideIndexes: [number, number];
}) {
  const { t } = useTranslation();
  if (rows.length === 0) {
    return <p className="muted-copy">{t("comparisonNoChangedSections")}</p>;
  }
  return (
    <div className="comparison-section-list">
      {rows.map((row) => (
        <section key={row.key}>
          <h3>{row.label}</h3>
          <div className="comparison-side-by-side">
            {sideIndexes.map((sideIndex, position) => (
              <div key={sideIndex}>
                <strong>{sides[position].analysis_date}</strong>
                <ComparisonValue value={row.values[sideIndex]} />
              </div>
            ))}
          </div>
        </section>
      ))}
    </div>
  );
}

function filterDecisionSections(
  sections: ComparisonSection[],
  changedOnly: boolean,
) {
  if (!changedOnly) return sections;
  return sections.filter(
    (section) => !comparisonValuesEqual(section.values[0], section.values[1]),
  );
}

function comparisonValuesEqual(
  left: ComparisonSection["values"][number] | undefined,
  right: ComparisonSection["values"][number] | undefined,
) {
  return stableJson(left ?? null) === stableJson(right ?? null);
}

function stableJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(stableJson).join(",")}]`;
  }
  if (value && typeof value === "object") {
    return `{${Object.entries(value)
      .sort(([left], [right]) => left.localeCompare(right))
      .map(([key, nested]) => `${JSON.stringify(key)}:${stableJson(nested)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value) ?? "undefined";
}

function productRow(
  key: string,
  label: string,
  sides: ResearchNodeComparison["sides"],
  value: (side: ComparisonSide) => unknown,
): ProductComparisonRow {
  return {
    key,
    label,
    values: [value(sides[0]), value(sides[1])],
  };
}

function filterProductRows(
  rows: ProductComparisonRow[],
  changedOnly: boolean,
) {
  if (!changedOnly) return rows;
  return rows.filter(
    (row) => stableJson(row.values[0]) !== stableJson(row.values[1]),
  );
}

function advancementSummary(t: TFunction, reasons: string[]): string {
  if (reasons.length === 0) return t("noInformationAdvancement");
  const labels: Record<string, string> = {
    admissible_observation: "advancementAdmissibleObservation",
    completed_stock_session: "advancementCompletedMarketSession",
    newly_completed_market_session: "advancementCompletedMarketSession",
    near_live_advisory: "advancementNearLiveAdvisory",
  };
  return reasons
    .map((reason) => t(labels[reason] ?? "advancementOther", { reason }))
    .join(", ");
}

function performanceComponentText(
  t: TFunction,
  label: string,
  component: NonNullable<ResearchNodeView["performance"]>["stock"],
): string {
  if (component.calculation) {
    return `${label}: ${formatPercent(component.calculation.unrounded_return)}`;
  }
  return `${label}: ${t(`performance_${component.status}`)}${
    component.reason ? ` · ${component.reason}` : ""
  }`;
}

function performanceComparisonText(
  t: TFunction,
  side: ComparisonSide,
): string | null {
  const performance = side.performance;
  if (!performance) return null;
  return [
    performanceComponentText(t, t("stockReturn"), performance.stock),
    ...(performance.benchmarks ?? []).map((benchmark) => {
      const summary = performanceComponentText(
        t,
        benchmark.name,
        benchmark.component,
      );
      return benchmark.reported_difference == null
        ? summary
        : `${summary} · ${t("reportedBenchmarkDifference")}: ${formatPercent(
            benchmark.reported_difference,
          )}`;
    }),
  ].join("\n");
}

function availabilityComparisonText(
  t: TFunction,
  side: ComparisonSide,
): string | null {
  const domains = side.research_availability?.domains ?? [];
  if (domains.length === 0) return null;
  return domains
    .map(
      (domain) =>
        `${t(`${domain.domain}Analyst`)}: ${t(
          `availability_${domain.status}`,
        )}`,
    )
    .join(", ");
}

function reassessmentComparisonText(
  t: TFunction,
  side: ComparisonSide,
): string | null {
  const entries = side.reassessment?.entries ?? [];
  if (entries.length === 0) return null;
  return entries
    .map(
      (entry) =>
        `${entry.component_id}: ${t(
          `reassessment_${entry.disposition}`,
        )} · ${entry.reason}`,
    )
    .join("\n");
}

function methodSummary(side: ComparisonSide): string | null {
  return [side.method_snapshot.llm_provider, side.method_snapshot.deep_model]
    .filter(Boolean)
    .join(" / ") || null;
}

function focusableElements(container: HTMLElement | null): HTMLElement[] {
  if (!container) return [];
  return Array.from(
    container.querySelectorAll<HTMLElement>(
      'button:not([disabled]), input:not([disabled]), summary, [href], [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((element) => {
    if (element.hasAttribute("hidden")) return false;
    const closedDetails = element.closest("details:not([open])");
    if (!closedDetails) return true;
    return closedDetails.querySelector(":scope > summary") === element;
  });
}

export default function Timeline() {
  const { t } = useTranslation();
  const pathname = usePathname();
  const isList = pathname === "/timelines";
  const instrument = decodeURIComponent(pathname.split("/").at(-1) ?? "");
  const [detail, setDetail] = useState<TimelineDetail | null>(null);
  const [timelines, setTimelines] = useState<ResearchTimelinePage | null>(null);
  const [listOffset, setListOffset] = useState(0);
  const [cycleOffset, setCycleOffset] = useState(0);
  const [query, setQuery] = useState("");
  const [showRetainedTrash, setShowRetainedTrash] = useState(false);
  const [comparisonNodes, setComparisonNodes] = useState<ResearchNodeView[]>([]);
  const [comparison, setComparison] = useState<ResearchNodeComparison | null>(null);
  const [comparisonBusy, setComparisonBusy] = useState(false);
  const [pendingNode, setPendingNode] = useState<ResearchNodeView | null>(null);
  const [lifecycleMode, setLifecycleMode] = useState<"trash" | "purge" | null>(null);
  const [replacementPrimary, setReplacementPrimary] = useState("");
  const [lifecycleBusy, setLifecycleBusy] = useState(false);
  const [error, setError] = useState("");
  const closeComparison = useCallback(() => setComparison(null), []);

  const cycles = detail?.timeline.cycles ?? [];
  const cycleTotal = detail?.timeline.cycle_total ?? 0;
  const cycleLimit = detail?.timeline.cycle_limit ?? CYCLE_PAGE_SIZE;
  const activeFullCycles = detail?.timeline.active_full_cycles ?? [];
  const filteredTimelines = useMemo(() => {
    const normalized = query.trim().toLocaleLowerCase();
    if (!normalized) return timelines?.items ?? [];
    return (timelines?.items ?? []).filter((item) =>
      [item.instrument, item.instrument_name, item.instrument_local_name]
        .filter(Boolean)
        .some((value) => String(value).toLocaleLowerCase().includes(normalized)),
    );
  }, [query, timelines]);

  useEffect(() => {
    let active = true;
    setError("");
    const fail = (cause: unknown) => {
      if (active) setError(cause instanceof Error ? cause.message : t("timelineLoadFailed"));
    };
    if (isList) {
      void api.timelines(50, listOffset).then((value) => active && setTimelines(value), fail);
    } else {
      void api.timeline(instrument, CYCLE_PAGE_SIZE, cycleOffset, showRetainedTrash ? "all" : "active").then((value) => active && setDetail(value), fail);
    }
    return () => { active = false; };
  }, [cycleOffset, instrument, isList, listOffset, showRetainedTrash, t]);

  useEffect(() => {
    setCycleOffset(0);
    setComparisonNodes([]);
    setComparison(null);
  }, [instrument]);

  const reloadDetail = async () => {
    setDetail(await api.timeline(instrument, CYCLE_PAGE_SIZE, cycleOffset, showRetainedTrash ? "all" : "active"));
  };

  const toggleComparison = (node: ResearchNodeView) => {
    setComparison(null);
    setComparisonNodes((current) => {
      if (current.some((item) => item.id === node.id)) return current.filter((item) => item.id !== node.id);
      return current.length < 2 ? [...current, node] : current;
    });
  };

  const compareSelected = async () => {
    if (comparisonNodes.length !== 2) return;
    const selections: ResearchNodeComparisonSelection[] = comparisonNodes.map((node) => ({ node_id: node.id, lifecycle_state: node.is_active ? "active" : "trashed" }));
    setComparisonBusy(true);
    try {
      setComparison(await api.compareResearchNodes(instrument, selections));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("timelineLoadFailed"));
    } finally {
      setComparisonBusy(false);
    }
  };

  const applyLifecycle = async () => {
    if (!pendingNode || !lifecycleMode) return;
    if (lifecycleMode === "trash" && pendingNode.research_kind === "full" && pendingNode.is_primary && activeFullCycles.some((cycle) => cycle.id !== pendingNode.id) && !replacementPrimary) {
      setError(t("selectReplacementCycle"));
      return;
    }
    setLifecycleBusy(true);
    try {
      if (lifecycleMode === "trash") await api.trashRuns([pendingNode.id], replacementPrimary ? { [pendingNode.id]: replacementPrimary } : {});
      else await api.purgeRuns([pendingNode.id]);
      setPendingNode(null);
      setLifecycleMode(null);
      setReplacementPrimary("");
      await reloadDetail();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("timelineLoadFailed"));
    } finally {
      setLifecycleBusy(false);
    }
  };

  if (isList) {
    return (
      <section>
        <header className="page-header">
          <div><p className="eyebrow">{t("researchTimeline")}</p><h1>{t("researchTimelines")}</h1><p className="subtitle">{t("researchTimelinesHint")}</p></div>
          <Link className="button primary" to="/runs/new">+ {t("newRun")}</Link>
        </header>
        <div className="workbench-toolbar">
          <label><span>{t("searchResearch")}</span><input value={query} onChange={(event) => setQuery(event.target.value)} /></label>
          <Link className="button" to="/runs">{t("executionHistory")}</Link>
        </div>
        {error && <div className="alert">{error}</div>}
        {!timelines && !error && <div className="loading">{t("loading")}</div>}
        <div className="timeline-list-grid">
          {filteredTimelines.map((item) => (
            <Link className={`timeline-summary-card ${item.timeline_warning ? "warning" : ""}`} to={`/timelines/${encodeURIComponent(item.instrument)}`} key={item.instrument}>
              <InstrumentIdentity ticker={item.instrument} instrumentName={item.instrument_name} instrumentLocalName={item.instrument_local_name} />
              <div className="timeline-summary-decision"><ResearchRatingBadge rating={item.primary_rating} /><Confidence value={item.primary_confidence} /></div>
              <dl><div><dt>{t("fullResearch")}</dt><dd>{item.full_cycle_count}</dd></div><div><dt>{t("incrementalResearch")}</dt><dd>{item.incremental_node_count ?? 0}</dd></div><div><dt>{t("latestResearch")}</dt><dd>{item.latest_analysis_date}</dd></div></dl>
              {item.timeline_warning && <span className="warning-copy">{t("fullResearchRecommended")}</span>}
            </Link>
          ))}
        </div>
        {timelines && timelines.total > timelines.limit && (
          <div className="pagination"><button className="button" disabled={listOffset === 0} onClick={() => setListOffset((value) => Math.max(0, value - timelines.limit))}>← {t("previous")}</button><span>{t("runRange", { start: listOffset + 1, end: Math.min(listOffset + filteredTimelines.length, timelines.total), total: timelines.total })}</span><button className="button" disabled={listOffset + (timelines.items?.length ?? 0) >= timelines.total} onClick={() => setListOffset((value) => value + timelines.limit)}>{t("next")} →</button></div>
        )}
      </section>
    );
  }

  return (
    <section>
      <header className="page-header research-header">
        <div><p className="eyebrow">{t("researchTimeline")}</p><InstrumentIdentity ticker={instrument} instrumentName={detail?.timeline.instrument_name} instrumentLocalName={detail?.timeline.instrument_local_name} prominent /></div>
        <div className="action-row"><Link className="button" to="/timelines">{t("allResearchTimelines")}</Link><button className="button" onClick={() => { setCycleOffset(0); setShowRetainedTrash((value) => !value); }}>{t(showRetainedTrash ? "hideRetainedTrash" : "showRetainedTrash")}</button></div>
      </header>
      {error && <div className="alert">{error}</div>}
      {!detail && !error && <div className="loading">{t("loading")}</div>}
      {detail?.timeline.timeline_warning && <div className="alert">{t("fullResearchRecommended")}</div>}
      {detail && cycles.length === 0 && <div className="empty-state">{t("noCommittedFullResearch")}</div>}
      {detail && (
        <aside className="comparison-tray" aria-label={t("comparisonSelection")}>
          <div><strong>{t("comparisonSelection")}</strong><span>{t("comparisonSelectionHint")}</span></div>
          <div className="comparison-selections">{comparisonNodes.map((node) => <button type="button" onClick={() => toggleComparison(node)} key={node.id}><KindBadge kind={node.research_kind} /> {node.analysis_date} · {node.decision?.rating ?? t("notRecorded")} ×</button>)}</div>
          <button className="button primary" disabled={comparisonNodes.length !== 2 || comparisonBusy} onClick={() => void compareSelected()}>{t("compareSelectedNodes")}</button>
        </aside>
      )}
      {comparison && (
        <NodeComparisonModal
          comparison={comparison}
          onClose={closeComparison}
        />
      )}
      <div className="research-cycle-list">
        {cycles.map((cycle) => (
          <section className={`research-cycle ${cycle.is_primary ? "primary" : ""}`} key={cycle.id}>
            <header className="research-cycle-header">
              <div><p className="eyebrow">{t("researchCycle")}</p><h2>{cycle.baseline.analysis_date}</h2></div>
              <div className="status-cluster">{cycle.is_primary && <span className="status-pill primary">{t("primaryCycle")}</span>}{cycle.cycle_warning && <span className="status-pill warning">{t("fullResearchRecommended")}</span>}{!cycle.is_primary && cycle.baseline.is_active && <button className="button compact-button" onClick={() => void api.selectPrimaryCycle(instrument, cycle.id).then(setDetail)}>{t("makePrimary")}</button>}</div>
            </header>
            <div className="cycle-rail">
              <NodeCard node={cycle.baseline} selected={comparisonNodes.some((item) => item.id === cycle.baseline.id)} comparisonFull={comparisonNodes.length === 2} onToggleComparison={toggleComparison} onLifecycle={(node, mode) => { setPendingNode(node); setLifecycleMode(mode); setReplacementPrimary(""); }} onReload={reloadDetail} />
              {(cycle.increments ?? []).map((node) => <NodeCard node={node} selected={comparisonNodes.some((item) => item.id === node.id)} comparisonFull={comparisonNodes.length === 2} onToggleComparison={toggleComparison} onLifecycle={(target, mode) => { setPendingNode(target); setLifecycleMode(mode); setReplacementPrimary(""); }} onReload={reloadDetail} key={node.id} />)}
            </div>
          </section>
        ))}
      </div>
      {detail && cycleTotal > cycleLimit && (
        <div className="pagination"><button className="button" disabled={cycleOffset === 0} onClick={() => setCycleOffset((value) => Math.max(0, value - cycleLimit))}>← {t("previous")}</button><span>{t("runRange", { start: cycleOffset + 1, end: Math.min(cycleOffset + cycles.length, cycleTotal), total: cycleTotal })}</span><button className="button" disabled={cycleOffset + cycles.length >= cycleTotal} onClick={() => setCycleOffset((value) => value + cycleLimit)}>{t("next")} →</button></div>
      )}
      {pendingNode && lifecycleMode && (
        <ConfirmDialog title={t(lifecycleMode === "purge" ? "purgeResearchTitle" : pendingNode.research_kind === "full" ? "cycleTrashTitle" : "nodeTrashTitle")} confirmLabel={t(lifecycleMode === "purge" ? "confirmPurge" : "confirmTimelineTrash")} cancelLabel={t("cancel")} busy={lifecycleBusy} onCancel={() => { setPendingNode(null); setLifecycleMode(null); }} onConfirm={() => void applyLifecycle()}>
          <p>{t(lifecycleMode === "purge" ? "purgeResearchImpact" : pendingNode.research_kind === "full" ? "fullOwnsCycle" : "incrementalTrashImpact")}</p>
          {lifecycleMode === "trash" && pendingNode.research_kind === "full" && pendingNode.is_primary && activeFullCycles.some((cycle) => cycle.id !== pendingNode.id) && (
            <label>{t("replacementPrimaryCycle")}<select value={replacementPrimary} onChange={(event) => setReplacementPrimary(event.target.value)}><option value="">{t("selectReplacementCycle")}</option>{activeFullCycles.filter((cycle) => cycle.id !== pendingNode.id).map((cycle) => <option key={cycle.id} value={cycle.id}>{cycle.analysis_date} · {cycle.rating ?? t("notRecorded")} · {cycle.confidence == null ? t("notRecorded") : `${Math.round(cycle.confidence * 100)}%`}</option>)}</select></label>
          )}
        </ConfirmDialog>
      )}
    </section>
  );
}

function formatPercent(value: number) {
  return new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: 2 }).format(value);
}
