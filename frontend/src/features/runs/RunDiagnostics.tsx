import ConnectionHistory from "./ConnectionHistory";
import { useTranslation } from "react-i18next";
import type { RunDetail, RunEvent, ResearchArtifact, EvidenceBundle } from "../../shared/api/client";
import { formatResearchDate } from "../../shared/researchDate";
import { researchLocation } from "../research/researchLinks";
import { Link } from "../../app/router";
import RunMetricsPanel from "./RunMetricsPanel";
import StatusBadge from "../../shared/StatusBadge";
import JsonRecord from "../../shared/JsonRecord";
import DiagnosticEvents from "./DiagnosticEvents";
import DiagnosticSnapshot from "./DiagnosticSnapshot";

export default function RunDiagnostics({ detail, events, artifacts, evidence }: {
  detail: RunDetail; events: RunEvent[]; artifacts: ResearchArtifact[]; evidence?: EvidenceBundle | null;
}) {
  const { t, i18n } = useTranslation();
  const { run, result, research_node: node } = detail;
  const bundle = evidence ?? result?.evidence;
  const reports = result?.reports ?? {};
  const reading = (view: string, report?: string, hash?: string) => {
    const url = new URL(researchLocation(run), 'http://local'); url.searchParams.set('view', view);
    if (report) url.searchParams.set('report', report);
    if (hash) url.hash = hash;
    return `${url.pathname}${url.search}${url.hash}`;
  };
  const time = (value: string | undefined | null) => formatResearchDate(value, i18n.language);
  return <section className="diagnostics-view" id="run-view-diagnostics" aria-labelledby="run-tab-diagnostics" role="region">
    <section className="diagnostic-block">
      <h2>{t("diagnosticOverview")}</h2>
      <dl className="diagnostic-fields">
        <div><dt>{t('runId')}</dt><dd><code>{run.id}</code></dd></div>
        <div><dt>{t('status')}</dt><dd><StatusBadge status={run.status} /></dd></div>
        <div><dt>{t('startedAt')}</dt><dd>{time(run.started_at)}</dd></div>
        <div><dt>{t('finishedAt')}</dt><dd>{time(run.finished_at)}</dd></div>
      </dl>
      <JsonRecord label={t('diagnosticOverview')} value={run} />
    </section>
    <RunMetricsPanel metrics={run.metrics} attempts={detail.attempts ?? []} events={events} artifacts={artifacts} />
    <section className="diagnostic-block">
      <h2>{t('diagnosticRecoveries')}</h2>
      {result?.recoveries?.length ? result.recoveries.map((item, index) => <article className="diagnostic-recovery" key={`${item.node}:${item.attempt}:${index}`}>
        <header><time>{time(item.recovered_at)}</time><code>{item.node}</code><span>{t('attempt')} {item.attempt}</span></header>
        <dl className="diagnostic-fields">
          <div><dt>{t('recoveryReason')}</dt><dd>{item.initial_reason_code}</dd></div>
          <div><dt>{t('generationMethod')}</dt><dd>{item.recovery_method}</dd></div>
          <div><dt>{t('retryCount')}</dt><dd>{item.retry_count}</dd></div>
          <div><dt>{t('validationIssues')}</dt><dd>{item.validation_issue_codes?.length ? item.validation_issue_codes.join(', ') : t('none')}</dd></div>
        </dl>
        <JsonRecord label={`${item.node} · ${item.attempt}`} value={item} />
      </article>) : <p>{t('noRecoveries')}</p>}
    </section>
    <DiagnosticSnapshot label={t('diagnosticConfiguration')} snapshot={run.config_snapshot} />
    <ConnectionHistory snapshot={run.method_snapshot} researchKind={run.research_kind} language={i18n.language} />
    <DiagnosticSnapshot label={t('diagnosticMethod')} snapshot={run.method_snapshot} />
    <section className="diagnostic-block" aria-labelledby="diagnostic-records">
      <h2 id="diagnostic-records">{t('diagnosticRecords')}</h2>
      <h3>{t('reports')}</h3>
      {Object.entries(reports).map(([name, report]) => <article className="diagnostic-index-row" key={name}>{run.is_research_node ? <Link className="text-link" to={reading('reports', name)}>{t(`${name}Analyst`, { defaultValue: name })}</Link> : <span>{t(`${name}Analyst`, {defaultValue: name})}</span>}<JsonRecord label={name} value={report} /></article>)}
      {!Object.keys(reports).length && <p>{t('notRecorded')}</p>}
      <h3>{t('evidence')}</h3>
      {bundle ? <p>{run.is_research_node ? <Link className="text-link" to={reading('evidence')}>{t('evidence')}</Link> : t('evidence')} · {t('recordCount', { count: bundle.items.length })}</p> : <p>{t('notRecorded')}</p>}
      <JsonRecord label={t('evidence')} value={bundle} />
      <h3>{t('researchNode')}</h3>
      {node ? <><dl className="diagnostic-fields"><div><dt>{t('selectedCutoff')}</dt><dd>{node.analysis_date}</dd></div><div><dt>{t('researchCycle')}</dt><dd><code>{node.cycle_id}</code></dd></div></dl>
        <p><Link className="text-link" to={researchLocation(run)}>{t('openResearch')}</Link>{node.full_baseline_run_id && <> · <Link className="text-link" to={`/timelines/${encodeURIComponent(node.instrument)}?node=${encodeURIComponent(node.full_baseline_run_id)}${run.trashed_at ? '&trash_state=all' : ''}`}>{t('fullBaseline')}</Link> · <Link className="text-link" to={reading('reassessment')}>{t('reassessment')}</Link></>}</p>
      </> : <p>{t('notRecorded')}</p>}
      <JsonRecord label={t('researchNode')} value={node} />
      <h3>{t('researchArtifacts')}</h3>
      {artifacts.map(artifact => <article className="diagnostic-index-row" key={artifact.id}>
        <header><strong>{artifact.stage} · {artifact.role}{artifact.round != null ? ` · ${t('round')} ${artifact.round}` : ''}</strong><time>{time(artifact.created_at)}</time><span>{t('attempt')} {artifact.attempt}</span></header>
        {run.is_research_node && run.research_kind !== 'incremental' && <Link className="text-link" to={artifact.stage === 'analyst' ? reading('reports', artifact.role) : reading('deliberation', undefined, `deliberation-report-${artifact.id}`)}>{t('openExistingResearch')}</Link>}
        <JsonRecord label={`${artifact.stage} · ${artifact.role} · ${artifact.id}`} value={artifact} />
      </article>)}
      {!artifacts.length && <p>{t('notRecorded')}</p>}
    </section>
    <DiagnosticEvents events={events} />
  </section>;
}
