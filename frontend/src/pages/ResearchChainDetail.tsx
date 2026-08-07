import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api, type ResearchChain } from "../api/client";
import { Link, useParams } from "../router";

export default function ResearchChainDetail() {
  const { t } = useTranslation();
  const { chainId = "" } = useParams();
  const [chain, setChain] = useState<ResearchChain | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    void api.researchChain(chainId).then(setChain).catch((cause) => {
      setError(cause instanceof Error ? cause.message : t("error"));
    });
  }, [chainId, t]);

  if (!chain) return <div className="loading">{error || t("loading")}</div>;
  const revision = chain.current_revision;
  if (!revision) return <div className="alert">{t("error")}</div>;
  const state = revision.current_state;
  const evidenceByRef = new Map(
    revision.evidence_snapshot.bundle.items.map((item) => [item.ref, item]),
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
          <a
            className="button primary"
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
        <ol>
          {(chain.revisions ?? []).map((item) => (
            <li key={item.id}>
              {item.cutoff} · {item.execution_strategy} · {item.outcome}
            </li>
          ))}
        </ol>
        <p>
          {t("llmCalls")}: {(revision.metrics?.llm_calls ?? 0).toLocaleString()} · {t("toolCalls")}: {(revision.metrics?.tool_calls ?? 0).toLocaleString()} · {t("inputTokens")}: {(revision.metrics?.input_tokens ?? 0).toLocaleString()} · {t("outputTokens")}: {(revision.metrics?.output_tokens ?? 0).toLocaleString()} · {t("cumulativeActiveTime")}: {(revision.metrics?.wall_time_seconds ?? 0).toFixed(1)}s
        </p>
      </article>
    </section>
  );
}
