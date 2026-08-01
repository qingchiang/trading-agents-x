import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  DecisionNumericAuditAppendix,
  NumericAuditOmission,
  NumericAuditSnapshot,
} from "../api/client";

export default function NumericAuditAppendixView({
  appendix,
}: {
  appendix: DecisionNumericAuditAppendix;
}) {
  const { t } = useTranslation();
  const defaultPhase = appendix.snapshots.some((item) => item.phase === "repair")
    ? "repair"
    : appendix.snapshots.at(-1)?.phase;
  const [phase, setPhase] = useState(defaultPhase);
  const omissions = appendix.omitted_components ?? [];
  const snapshot = useMemo(
    () =>
      appendix.snapshots.find((item) => item.phase === phase) ??
      appendix.snapshots.at(-1),
    [appendix.snapshots, phase],
  );

  return (
    <details className="numeric-audit-appendix">
      <summary>
        <span>{t("unverifiedNumericDrafts")}</span>
        <span className={`numeric-audit-status status-${appendix.status}`}>
          {t(`numericAppendixStatus.${appendix.status}`)}
        </span>
      </summary>
      <div className="numeric-audit-appendix-body">
        <p className="numeric-audit-boundary" role="note">
          {t("unverifiedNumericBoundary")}
        </p>

        {omissions.length > 0 && (
          <section className="numeric-audit-omissions">
            <h3>{t("omittedNumericComponents")}</h3>
            <ul>
              {omissions.map((item) => (
                <li key={item.component_path}>
                  <strong>{omissionLabel(item, t)}</strong>
                  <code>{item.component_path}</code>
                  <IssueCodes issues={item.issue_codes} />
                </li>
              ))}
            </ul>
          </section>
        )}

        {appendix.snapshots.length > 1 && (
          <div className="numeric-snapshot-tabs" role="tablist">
            {[...appendix.snapshots].reverse().map((item) => (
              <button
                type="button"
                role="tab"
                aria-selected={snapshot?.phase === item.phase}
                className={snapshot?.phase === item.phase ? "active" : ""}
                onClick={() => setPhase(item.phase)}
                key={item.phase}
              >
                {t(`numericSnapshotPhase.${item.phase}`)}
              </button>
            ))}
          </div>
        )}

        {snapshot && <NumericSnapshotView snapshot={snapshot} />}
      </div>
    </details>
  );
}

function omissionLabel(
  item: NumericAuditOmission,
  t: (key: string, options?: Record<string, unknown>) => string,
): string {
  const parts: string[] = [];
  if (item.scenario_kind) {
    parts.push(
      t(
        {
          base: "scenarioBase",
          bull: "scenarioBull",
          bear: "scenarioBear",
        }[item.scenario_kind],
      ),
    );
  }
  parts.push(t(`numericOmissionComponent.${item.component_type}`));
  if (item.reference_label) parts.push(item.reference_label);
  return parts.join(" · ");
}

function NumericSnapshotView({ snapshot }: { snapshot: NumericAuditSnapshot }) {
  const { t } = useTranslation();
  return (
    <section className="numeric-snapshot" role="tabpanel">
      <div className="numeric-snapshot-meta">
        <span>
          {t("generationMethod")}: <code>{snapshot.method}</code>
        </span>
        <span>
          {t("failureReason")}: <code>{snapshot.reason_code}</code>
        </span>
        <span>
          {t("schemaValid")}: {snapshot.schema_valid ? t("yes") : t("no")}
        </span>
      </div>
      <IssueCodes issues={snapshot.validation_issues ?? []} />
      {snapshot.candidate ? (
        snapshot.schema_valid ? (
          <StructuredCandidate candidate={snapshot.candidate} />
        ) : (
          <pre className="numeric-candidate-json">
            {JSON.stringify(snapshot.candidate, null, 2)}
          </pre>
        )
      ) : (
        <p className="numeric-candidate-omitted">
          {snapshot.candidate_omitted === "oversize"
            ? t("numericCandidateOversize", {
                digest: snapshot.candidate_digest ?? "—",
              })
            : t("numericCandidateUnparseable")}
        </p>
      )}
    </section>
  );
}

function StructuredCandidate({ candidate }: { candidate: Record<string, unknown> }) {
  const { t } = useTranslation();
  const groups = [
    ["valuation_assessment", "numericCandidateGroup.valuation"],
    ["scenario_reference_ranges", "numericCandidateGroup.scenarios"],
    ["market_reference_levels", "numericCandidateGroup.references"],
    ["calculation_records", "numericCandidateGroup.calculations"],
  ] as const;
  const visible = groups.filter(([key]) => candidate[key] != null);
  return (
    <div className="numeric-candidate-groups">
      {visible.map(([key, label]) => (
        <section key={key}>
          <h4>{t(label)}</h4>
          <pre>{JSON.stringify(candidate[key], null, 2)}</pre>
        </section>
      ))}
      {visible.length === 0 && (
        <pre>{JSON.stringify(candidate, null, 2)}</pre>
      )}
    </div>
  );
}

function IssueCodes({ issues }: { issues: string[] }) {
  const { t } = useTranslation();
  if (issues.length === 0) return null;
  return (
    <div className="numeric-issue-codes" aria-label={t("validationIssues")}>
      {issues.map((issue) => (
        <code key={issue}>{issue}</code>
      ))}
    </div>
  );
}
