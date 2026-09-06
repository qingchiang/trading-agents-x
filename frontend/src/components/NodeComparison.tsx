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
import ConfirmDialog from "./ConfirmDialog";
import { InstrumentIdentity } from "./Instruments";
import ResearchRatingBadge from "./ResearchRatingBadge";
import ResearchKindBadge from "./ResearchKindBadge";
import { Link, usePathname } from "../router";
import { localizePerformanceReason, researchConfidenceLabel } from "../i18n";

function Confidence({ value }: { value?: "low" | "medium" | "high" | null }) {
  const { t } = useTranslation();
  return (
    <span className="confidence-value">
      {value == null
        ? t("notRecorded")
        : researchConfidenceLabel(t, value)}
    </span>
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
    (comparisonValue.value === "low" ||
      comparisonValue.value === "medium" ||
      comparisonValue.value === "high")
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

export default function NodeComparisonModal({
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
        "decision-outcome",
        t("decisionOutcome"),
        comparison.sides,
        (side) =>
          side.decision_outcome
            ? t(`decisionOutcome_${side.decision_outcome}`)
            : t("decisionOutcomeNotRecorded"),
      ),
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
          <div className="comparison-modal-title">
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
            <label className="comparison-changed-toggle">
              <input
                type="checkbox"
                checked={changedOnly}
                onChange={(event) => setChangedOnly(event.target.checked)}
              />
              {t("showChangedOnly")}
            </label>
            <button
              type="button"
              className="button compact-button"
              onClick={() => setSwapped((value) => !value)}
            >
              {t("swapComparisonSides")}
            </button>
            <button
              ref={closeRef}
              type="button"
              className="button compact-button"
              onClick={onClose}
            >
              {t("close")}
            </button>
          </div>
        </header>

        <div
          className="comparison-modal-scroll"
          aria-label={t("nodeComparison")}
          tabIndex={0}
        >
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
            <th aria-label={t("decisionSection")}>
              <span className="sr-only">{t("decisionSection")}</span>
            </th>
            {sides.map((side) => (
              <th key={side.node_id}>
                <ResearchKindBadge
                  kind={side.research_kind}
                  methodSnapshot={side.method_snapshot}
                />
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
    component.reason ? ` · ${localizePerformanceReason(t, component.reason)}` : ""
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


function formatPercent(value: number) { return new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: 2 }).format(value); }
