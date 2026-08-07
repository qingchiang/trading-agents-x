import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type ResearchChain } from "../api/client";
import { Link, useNavigate, useParams } from "../router";

export default function ResearchChainDetail() {
  const { t } = useTranslation();
  const { chainId = "" } = useParams();
  const navigate = useNavigate();
  const [chain, setChain] = useState<ResearchChain | null>(null);
  const [error, setError] = useState("");
  const [updateCutoff, setUpdateCutoff] = useState("");
  const [updating, setUpdating] = useState(false);

  useEffect(() => {
    void api.researchChain(chainId).then(setChain).catch((cause) => {
      setError(cause instanceof Error ? cause.message : t("error"));
    });
  }, [chainId, t]);

  if (!chain) return <div className="loading">{error || t("loading")}</div>;
  const revision = chain.current_revision;
  if (!revision) return <div className="alert">{t("error")}</div>;
  const state = revision.current_state;
  const updateAudit = revision.research_update_audit;
  const emptyMetrics = {
    llm_calls: 0,
    tool_calls: 0,
    input_tokens: 0,
    output_tokens: 0,
    wall_time_seconds: 0,
  };
  const boundedMetrics = { ...emptyMetrics, ...(updateAudit?.bounded_metrics ?? {}) };
  const fullMetrics = { ...emptyMetrics, ...(updateAudit?.full_metrics ?? {}) };
  const boundedCoverage = updateAudit?.coverage as
    | {
        supports_no_material_change?: boolean;
        limitations?: string[];
        domains?: Array<{ domain?: string; source?: string; status?: string }>;
      }
    | undefined;
  const candidateSummary = updateAudit?.candidate?.update_summary as
    | { summary?: string }
    | undefined;
  const nextCutoff = updateCutoff || dayAfter(revision.cutoff);
  const evidenceByRef = new Map(
    revision.evidence_snapshot.bundle.items.map((item) => [item.ref, item]),
  );
  const sourceLineageByVersion = new Map(
    (revision.evidence_snapshot.source_record_lineage ?? []).map((item) => [item.version_id, item]),
  );

  return (
    <section>
      <header className="page-header">
        <div>
          <Link className="back-link" to="/research">
            ← {t("researchChains")}
          </Link>
          <h1>{state.instrument}</h1>
          <p className="subtitle">
            {revision.cutoff} · {revision.execution_strategy}
            {chain.is_primary ? ` · ${t("primaryChain")}` : ""}
          </p>
        </div>
        <div className="action-row">
          <form
            className="action-row"
            onSubmit={(event) => {
              event.preventDefault();
              setUpdating(true);
              setError("");
              void api
                .updateResearchChain(
                  chain.id,
                  {
                    baseline_revision_id: revision.id,
                    analysis_date: nextCutoff,
                  },
                  createIdempotencyKey(),
                )
                .then((run) => navigate(`/runs/${run.id}`))
                .catch((cause) => {
                  setError(cause instanceof Error ? cause.message : t("error"));
                  setUpdating(false);
                });
            }}
          >
            <input
              aria-label={t("updateCutoff")}
              type="date"
              min={dayAfter(revision.cutoff)}
              value={nextCutoff}
              onChange={(event) => setUpdateCutoff(event.target.value)}
            />
            <button className="button primary" disabled={updating}>
              {updating ? t("loading") : t("updateResearch")}
            </button>
          </form>
          <a
            className="button"
            href={`/api/v1/research-revisions/${revision.id}/export?format=package`}
          >
            {t("exportPackage")}
          </a>
          {revision.producing_run_id && (
            <Link className="button" to={`/runs/${revision.producing_run_id}`}>
              {t("fullReports")}
            </Link>
          )}
        </div>
      </header>

      <article className="panel reader-panel">
        <p className="eyebrow">{t("currentResearchState")}</p>
        <h2>{state.opinion.rating}</h2>
        <p>{state.opinion.thesis}</p>
        <h3>{t("updateSummary")}</h3>
        <p>{revision.update_summary.summary}</p>
        <p>
          {t("confidence")}: <strong>{state.opinion.confidence}</strong>
        </p>
        <h3>{t("thesis")}</h3>
        <ul>
          {state.claims.map((claim) => (
            <li key={claim.id}>
              {claim.statement} <small>({claim.confidence})</small>
            </li>
          ))}
        </ul>
        <h3>{t("risks")}</h3>
        <ul>{(state.risks ?? []).map((item) => <li key={item.statement}>{item.statement}</li>)}</ul>
        <h3>{t("catalysts")}</h3>
        <ul>{(state.catalysts ?? []).map((item) => <li key={item.statement}>{item.statement}</li>)}</ul>
        <h3>{t("invalidation")}</h3>
        <ul>{(state.invalidation_conditions ?? []).map((item) => <li key={item.statement}>{item.statement}</li>)}</ul>
        <h3>{t("scenarios")}</h3>
        <ul>{state.scenarios.map((scenario) => <li key={scenario.kind}><strong>{scenario.kind}</strong> · {scenario.likelihood} · {scenario.horizon}: {scenario.outcome}</li>)}</ul>
        <h3>{t("unresolved")}</h3>
        <ul>{(state.questions ?? []).map((question) => <li key={question.id}>{question.question}</li>)}</ul>
      </article>

      <article className="panel">
        <h2>{t("coverageLimitations")}</h2>
        {(revision.coverage.limitations ?? []).length ? (
          <ul>{(revision.coverage.limitations ?? []).map((item) => <li key={item}>{item}</li>)}</ul>
        ) : (
          <p>{t("noCoverageLimitations")}</p>
        )}
        <h3>{t("domainCoverage")}</h3>
        <ul>
          {(revision.coverage.domains ?? []).map((item) => (
            <li key={`${item.domain}-${item.source ?? ""}`}>
              <strong>{item.domain}</strong>{item.source ? ` / ${item.source}` : ""} · {item.status}
              {item.requirement ? ` · ${item.requirement}` : ""}
              {(item.limitations ?? []).length > 0 && (
                <ul>{(item.limitations ?? []).map((value) => <li key={value}>{value}</li>)}</ul>
              )}
            </li>
          ))}
        </ul>
        <p>
          <strong>
            {t(revision.coverage.supports_no_material_change === false
              ? "quietReassessmentBlocked"
              : "quietReassessmentSupported")}
          </strong>
        </p>
        <h3>{t("sourceWatermarks")}</h3>
        <ul>
          {(revision.evidence_snapshot.source_watermarks ?? []).map((item) => (
            <li key={`${item.source}-${item.scanned_start}-${item.scanned_end}`}>
              <strong>{item.source}</strong> · {item.scanned_start} – {item.scanned_end} · {item.status}
              {item.baseline_cutoff ? ` · ${t("baseline")}: ${item.baseline_cutoff}` : ""}
              {item.overlap_start ? ` · ${t("overlapStart")}: ${item.overlap_start}` : ""}
              {(item.limitations ?? []).length > 0 && (
                <ul>{(item.limitations ?? []).map((value) => <li key={value}>{value}</li>)}</ul>
              )}
            </li>
          ))}
        </ul>
        <h3>{t("sourceRecordVersions")}</h3>
        <ul>
          {(revision.evidence_snapshot.source_records ?? []).map((item) => {
            const lineage = sourceLineageByVersion.get(item.version_id);
            return (
              <li key={item.version_id}>
                <strong>{item.title}</strong> · <code>{item.version_id}</code> · {item.status} · {lineage?.lineage}
                <small>
                  {" "}{item.source} / {item.record_id} · {item.available_at}
                  {item.availability_basis ? ` · ${item.availability_basis}` : ""}
                  {item.native_record_id ? ` · native ${item.native_record_id}` : ""}
                  {item.adjustment ? ` · ${item.adjustment}` : ""}
                  {item.unit ? ` · ${item.unit}/${item.precision ?? "?"}` : ""}
                  {` · fallback ${String(item.fallback ?? false)}`}
                </small>
              </li>
            );
          })}
        </ul>
        <h3>{t("claimCoverage")}</h3>
        <ul>
          {(revision.coverage.claims ?? []).map((item) => (
            <li key={item.object_id}>
              <code>{item.object_id}</code> · {item.status}
              {(item.limitations ?? []).length > 0 && (
                <ul>{(item.limitations ?? []).map((value) => <li key={value}>{value}</li>)}</ul>
              )}
            </li>
          ))}
        </ul>
        <h3>{t("questionCoverage")}</h3>
        <ul>
          {(revision.coverage.questions ?? []).map((item) => (
            <li key={item.object_id}>
              <code>{item.object_id}</code> · {item.status}
              {(item.limitations ?? []).length > 0 && (
                <ul>{(item.limitations ?? []).map((value) => <li key={value}>{value}</li>)}</ul>
              )}
            </li>
          ))}
        </ul>
        <h3>{t("evidence")}</h3>
        <ul>
          {revision.evidence_snapshot.lineage.map((item) => {
            const evidence = evidenceByRef.get(item.evidence_ref);
            return (
              <li key={item.evidence_ref}>
                <code>{item.evidence_ref}</code> · {item.lineage} ·{" "}
                {evidence?.source} / {evidence?.evidence_type}
                {evidence?.content && <p>{evidence.content}</p>}
              </li>
            );
          })}
        </ul>
      </article>

      <article className="panel">
        <h2>{t("revisionHistory")}</h2>
        {updateAudit && (
          <section>
            <h3>{t("shadowFinding")}</h3>
            <ul>
              <li>{t("candidateOutcome")}: {updateAudit.candidate?.outcome ?? t("none")}</li>
              <li>{t("authoritativeStrategy")}: {updateAudit.authoritative_strategy}</li>
              <li>{t("escalationReason")}: {updateAudit.escalation_reason ?? t("none")}</li>
              <li>{t("shadowComparison")}: {updateAudit.comparison}</li>
              <li>
                {t("boundedWindows")}: {(updateAudit.checked_windows ?? []).length > 0
                  ? (updateAudit.checked_windows ?? []).map((item) => `${item.source} ${item.scanned_start}–${item.scanned_end} (${item.status})`).join("; ")
                  : t("none")}
              </li>
              <li>
                {t("boundedUpdateSummary")}: {candidateSummary?.summary ?? t("none")}
              </li>
              <li>
                {t("boundedCoverage")}: {boundedCoverage
                  ? `${String(boundedCoverage.supports_no_material_change ?? false)}; ${(boundedCoverage.domains ?? []).map((item) => `${item.domain ?? item.source ?? "?"} (${item.status ?? "?"})`).join("; ")}`
                  : t("none")}
                {(boundedCoverage?.limitations ?? []).length > 0 && (
                  <ul>{(boundedCoverage?.limitations ?? []).map((value) => <li key={value}>{value}</li>)}</ul>
                )}
              </li>
              <li>
                {t("boundedEvidenceLineage")}: {(updateAudit.evidence_lineage ?? []).length > 0
                  ? (updateAudit.evidence_lineage ?? []).map((item) => `${item.evidence_ref} (${item.lineage})`).join("; ")
                  : t("none")}
              </li>
              <li>
                {t("semanticAssessment")}: {updateAudit.semantic_assessment?.summary ?? t("none")}
                {(updateAudit.semantic_assessment?.relationships ?? []).length > 0 && (
                  <ul>
                    {(updateAudit.semantic_assessment?.relationships ?? []).map((item, index) => (
                      <li key={`${item.relationship}-${index}`}>
                        {item.relationship} · {[
                          ...(item.suggested_claim_ids ?? []),
                          ...(item.suggested_question_ids ?? []),
                        ].join(", ") || t("none")} · {item.evidence_refs.join(", ")}
                      </li>
                    ))}
                  </ul>
                )}
              </li>
              <li>
                {t("boundedWork")}: {boundedMetrics.llm_calls} {t("llmCalls")} · {boundedMetrics.tool_calls} {t("toolCalls")} · {boundedMetrics.input_tokens}/{boundedMetrics.output_tokens} {t("inputOutputTokens")} · {boundedMetrics.wall_time_seconds.toFixed(1)}s
              </li>
              <li>
                {t("fullWork")}: {fullMetrics.llm_calls} {t("llmCalls")} · {fullMetrics.tool_calls} {t("toolCalls")} · {fullMetrics.input_tokens}/{fullMetrics.output_tokens} {t("inputOutputTokens")} · {fullMetrics.wall_time_seconds.toFixed(1)}s
              </li>
            </ul>
          </section>
        )}
        <ol>
          {(chain.revisions ?? []).map((item) => (
            <li key={item.id}>
              {item.cutoff} · {item.execution_strategy} · {item.outcome}{" "}
              <a href={`/api/v1/research-revisions/${item.id}/export?format=json`}>
                {t("revisionExport")}
              </a>
              {item.producing_run_id && (
                <>
                  {" · "}
                  <Link to={`/runs/${item.producing_run_id}`}>
                    {t("producingRun")}
                  </Link>
                </>
              )}
            </li>
          ))}
        </ol>
        <h3>{t("stateDelta")}</h3>
        <ul>
          {(revision.delta?.claims ?? []).map((item) => (
            <li key={`claim-${item.object_id}`}>
              <code>{item.object_id}</code> · {item.change} · {item.identity_disposition}
            </li>
          ))}
          {(revision.delta?.questions ?? []).map((item) => (
            <li key={`question-${item.object_id}`}>
              <code>{item.object_id}</code> · {item.change} · {item.identity_disposition}
            </li>
          ))}
        </ul>
        <h3>{t("fundamentalMarketChanges")}</h3>
        <ul>
          {(revision.delta?.change_signals ?? []).map((item) => (
            <li key={`${item.kind}-${item.record_id}-${item.current_version_id ?? "none"}`}>
              <strong>{item.kind}</strong> · {item.domain} · <code>{item.record_id}</code>
              {item.boundary_label ? ` · ${item.boundary_label}: ${item.boundary_value}` : ""}
              {item.previous_value !== null && item.previous_value !== undefined
                ? ` · ${item.previous_value} → ${item.current_value}`
                : ""}
              {item.requires_full_analysis ? ` · ${t("requiresFullAnalysis")}` : ""}
              <p>{item.detail}</p>
            </li>
          ))}
        </ul>
        <p>
          {t("llmCalls")}: {(revision.metrics?.llm_calls ?? 0).toLocaleString()} · {t("toolCalls")}: {(revision.metrics?.tool_calls ?? 0).toLocaleString()} · {t("inputTokens")}: {(revision.metrics?.input_tokens ?? 0).toLocaleString()} · {t("outputTokens")}: {(revision.metrics?.output_tokens ?? 0).toLocaleString()} · {t("cumulativeActiveTime")}: {(revision.metrics?.wall_time_seconds ?? 0).toFixed(1)}s
        </p>
      </article>
    </section>
  );
}

function dayAfter(value: string) {
  const date = new Date(`${value}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + 1);
  return date.toISOString().slice(0, 10);
}

function createIdempotencyKey() {
  return globalThis.crypto?.randomUUID?.() ??
    `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
