import { useTranslation } from "react-i18next";
import JsonRecord from "./JsonRecord";

const fieldLabels: Record<string, string> = {
  profile: 'profile', llm_provider: 'provider', quick_model: 'quickModel', deep_model: 'deepModel', quick_reasoning_effort: 'quickReasoning', deep_reasoning_effort: 'deepReasoning',
  temperature: 'temperature', llm_max_retries: 'maxRetries', output_language: 'reportLanguage', enabled_roles: 'analysts', application_version: 'applicationVersion',
  schema_version: 'schemaVersion', research_schema_version: 'researchSchemaVersion', market_identity: 'marketIdentity', data_availability_policy: 'diagnosticPolicy',
};

export default function DiagnosticSnapshot({ label, snapshot }: { label: string; snapshot: Record<string, unknown> | null | undefined }) {
  const { t } = useTranslation();
  const scalar = (value: unknown): string => {
    if (value == null) return t('notRecorded');
    if (Array.isArray(value)) return value.map(scalar).join(', ') || '[]';
    if (typeof value === 'object') return Object.entries(value as Record<string, unknown>).map(([key, item]) => `${key}: ${scalar(item)}`).join(' · ');
    return String(value);
  };
  const display = (key: string, value: unknown) => {
    if (value == null) return t('notRecorded');
    if (key === 'output_language') return ({ en: 'English', 'zh-CN': '简体中文', zh: '简体中文', ja: '日本語' }[String(value)] ?? String(value));
    if (key === 'profile' && ['fast', 'standard', 'deep'].includes(String(value))) return t(String(value));
    if (key.endsWith('reasoning_effort') && value === 'provider_default') return t('providerDefault');
    if (key === 'enabled_roles' && Array.isArray(value)) return value.map(role => ['market', 'news', 'fundamentals', 'social'].includes(String(role)) ? t(`${role}Analyst`) : String(role)).join(', ');
    return scalar(value);
  };
  const fields = (keys: string[]) => <dl className="diagnostic-fields">{keys.map(key => <div key={key}><dt>{t(fieldLabels[key], { defaultValue: key })}</dt><dd>{display(key, snapshot?.[key])}</dd></div>)}</dl>;
  const detailKeys = ['prompt_versions', 'data_routes', 'thresholds', 'data_config'].filter(key => snapshot && key in snapshot);
  return <section className="diagnostic-block">
    <h2>{label}</h2>
    <p className="secondary-line">{t('sourceSnapshot')}: {label}</p>
    {!snapshot ? <p>{t('notRecorded')}</p> : <>
      {fields(['profile', 'llm_provider', 'output_language', 'enabled_roles'].filter(key => key in snapshot))}
      <div className="diagnostic-models">{['quick', 'deep'].map(role => <section key={role}><h3>{t(role === 'quick' ? 'quickModel' : 'deepModel')}</h3>{fields([`${role}_model`, `${role}_reasoning_effort`])}</section>)}</div>
      {fields(['temperature', 'llm_max_retries', 'application_version', 'schema_version', 'research_schema_version', 'market_identity', 'data_availability_policy'].filter(key => key in snapshot))}
      {detailKeys.length > 0 && <details className="diagnostic-disclosure"><summary>{t('rawDetails')}</summary>
        {detailKeys.map(key => <JsonRecord label={key} value={snapshot[key]} key={key} />)}
      </details>}
    </>}
    <JsonRecord label={label} value={snapshot} />
  </section>;
}
