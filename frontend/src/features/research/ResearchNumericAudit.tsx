import { lazy, Suspense, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { api, type EvidenceBundle, type RunDetail } from '../../shared/api/client';
import { buildEvidenceReferenceIndex } from './evidence';
import NumericAuditAppendixView from './NumericAuditAppendixView';

const EvidenceSourceDrawer = lazy(() => import('./EvidenceSourceDrawer'));

/** Baseline diagnostics are read-only context, never a replacement for the current audit. */
export default function ResearchNumericAudit({ detail, evidence }: { detail: RunDetail; evidence?: EvidenceBundle | null }) {
  const { t } = useTranslation();
  const baselineId = detail.run.research_kind === 'incremental'
    ? detail.run.full_baseline_run_id ?? detail.research_node?.full_baseline_run_id
    : null;
  const identity = `${detail.run.id}:${baselineId ?? ''}`;
  const [loaded, setLoaded] = useState<{ identity: string; detail?: RunDetail; error?: string } | null>(null);
  const [revision, setRevision] = useState(0);
  const [source, setSource] = useState<{ identity: string; ref: string } | null>(null);
  useEffect(() => {
    if (!baselineId) return;
    let active = true;
    setLoaded(null);
    void api.run(baselineId).then(value => {
      if (value.run.id !== baselineId || value.run.request.ticker !== detail.run.request.ticker || value.run.research_kind === 'incremental') throw new Error('Unexpected full baseline');
      if (active) setLoaded({ identity, detail: value });
    }).catch(error => { if (active) setLoaded({ identity, error: String(error) }); });
    return () => { active = false; };
  }, [baselineId, identity, detail.run.request.ticker, revision]);
  const baseline = loaded?.identity === identity ? loaded.detail : undefined;
  const error = loaded?.identity === identity ? loaded.error : undefined;
  const index = useMemo(() => buildEvidenceReferenceIndex(evidence ?? detail.result?.evidence ?? null, baseline?.result?.evidence ?? null), [evidence, detail.result?.evidence, baseline]);
  return <>
    {baselineId && !baseline && !error && <p role="status">{t('baselineAuditLoading')}</p>}
    {error && <p className="alert" role="alert">{t('baselineAuditUnavailable')} <button className="button" onClick={() => setRevision(value => value + 1)}>{t('retryLoad')}</button></p>}
    <NumericAuditAppendixView
      appendix={detail.result?.numeric_audit}
      calculationRecords={detail.result?.decision?.calculation_records ?? []}
      baseline={baseline ? { runId: baseline.run.id, date: baseline.run.request.analysis_date, calculations: baseline.result?.decision?.calculation_records ?? [], appendix: baseline.result?.numeric_audit } : undefined}
      evidenceIndex={index} onEvidence={ref => setSource({ identity, ref })}
    />
    {source?.identity === identity && <Suspense fallback={null}><EvidenceSourceDrawer evidenceRef={source.ref} evidenceIndex={index} onClose={() => setSource(null)} /></Suspense>}
  </>;
}
