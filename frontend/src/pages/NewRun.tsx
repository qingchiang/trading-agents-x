import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  api,
  type AnalysisRequest,
  type Capabilities,
} from "../api/client";
import { useNavigate } from "../router";

const analystKeys = ["market", "social", "news", "fundamentals"] as const;

export default function NewRun() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [ticker, setTicker] = useState("");
  const [analysisDate, setAnalysisDate] = useState(today());
  const [profile, setProfile] = useState<"fast" | "standard" | "deep">(
    "standard",
  );
  const [analysts, setAnalysts] = useState<string[]>([...analystKeys]);
  const [provider, setProvider] = useState("openai");
  const [quickModel, setQuickModel] = useState("");
  const [deepModel, setDeepModel] = useState("");
  const [quickCustomModel, setQuickCustomModel] = useState("");
  const [deepCustomModel, setDeepCustomModel] = useState("");
  const [quickReasoning, setQuickReasoning] = useState("provider_default");
  const [deepReasoning, setDeepReasoning] = useState("provider_default");
  const [outputLanguage, setOutputLanguage] = useState<
    "en" | "zh-CN" | "ja"
  >("en");
  const [provenance, setProvenance] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const submission = useRef<{ fingerprint: string; key: string } | null>(null);

  useEffect(() => {
    void api
      .capabilities()
      .then((data) => {
        setCapabilities(data);
        setProfile(
          data.defaults.profile as "fast" | "standard" | "deep",
        );
        setProvider(data.defaults.llm_provider);
        setQuickModel(data.defaults.quick_model);
        setDeepModel(data.defaults.deep_model);
        setQuickReasoning(
          data.defaults.quick_reasoning_effort ?? "provider_default",
        );
        setDeepReasoning(
          data.defaults.deep_reasoning_effort ?? "provider_default",
        );
        setOutputLanguage(normalizeReportLanguage(data.defaults.output_language));
        setProvenance(data.defaults.provenance);
      })
      .catch((cause) => {
        setError(cause instanceof Error ? cause.message : t("error"));
      });
  }, [t]);

  const providerConfig = capabilities?.providers[provider];
  const quickOptions = useMemo(
    () => providerConfig?.quick_models ?? [],
    [providerConfig],
  );
  const deepOptions = useMemo(
    () => providerConfig?.deep_models ?? [],
    [providerConfig],
  );

  const changeProvider = (next: string) => {
    setProvider(next);
    const config = capabilities?.providers[next];
    setQuickModel(String(config?.quick_models?.[0]?.value ?? ""));
    setDeepModel(String(config?.deep_models?.[0]?.value ?? ""));
    setQuickCustomModel("");
    setDeepCustomModel("");
    setQuickReasoning("provider_default");
    setDeepReasoning("provider_default");
  };

  const toggleAnalyst = (key: string) => {
    setAnalysts((current) =>
      current.includes(key)
        ? current.filter((item) => item !== key)
        : [...current, key],
    );
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!analysts.length) {
      setError(t("selectAnalystError"));
      return;
    }
    if (
      (quickModel === "custom" && !quickCustomModel.trim()) ||
      (deepModel === "custom" && !deepCustomModel.trim())
    ) {
      setError(t("customModel"));
      return;
    }
    setSubmitting(true);
    setError("");
    const resolvedQuickModel =
      quickModel === "custom" ? quickCustomModel.trim() : quickModel;
    const resolvedDeepModel =
      deepModel === "custom" ? deepCustomModel.trim() : deepModel;
    const payload: AnalysisRequest = {
      ticker,
      analysis_date: analysisDate,
      asset_type: null,
      profile,
      analysts: analysts as AnalysisRequest["analysts"],
      llm_provider: provider,
      quick_model: resolvedQuickModel,
      deep_model: resolvedDeepModel,
      quick_reasoning_effort: quickReasoning,
      deep_reasoning_effort: deepReasoning,
      output_language: outputLanguage,
      provenance,
    };
    try {
      const fingerprint = JSON.stringify(payload);
      if (submission.current?.fingerprint !== fingerprint) {
        submission.current = {
          fingerprint,
          key: createIdempotencyKey(),
        };
      }
      const run = await api.createRun(payload, submission.current.key);
      navigate(`/runs/${run.id}`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : t("error"));
      setSubmitting(false);
    }
  };

  return (
    <section>
      <header className="page-header">
        <div>
          <p className="eyebrow">{t("requestEyebrow")}</p>
          <h1>{t("newRun")}</h1>
          <p className="subtitle">{t("evidenceSnapshotHint")}</p>
        </div>
      </header>
      <form className="run-form" onSubmit={submit}>
        <article className="panel form-section">
          <span className="step">01</span>
          <div className="form-section-body">
            <h2>{t("instrumentCutoff")}</h2>
            <div className="form-grid two">
              <label>
                {t("ticker")}
                <input
                  required
                  autoFocus
                  value={ticker}
                  onChange={(event) => setTicker(event.target.value)}
                  placeholder="7203.T"
                />
                <small>{t("tickerHint")}</small>
              </label>
              <label>
                {t("analysisDate")}
                <input
                  required
                  type="date"
                  value={analysisDate}
                  onChange={(event) => setAnalysisDate(event.target.value)}
                />
                <small>{t("cutoffHint")}</small>
              </label>
            </div>
          </div>
        </article>

        <article className="panel form-section">
          <span className="step">02</span>
          <div className="form-section-body">
            <h2>{t("profile")}</h2>
            <div className="profile-grid">
              {(["fast", "standard", "deep"] as const).map((key) => (
                <button
                  type="button"
                  className={`profile-card ${profile === key ? "selected" : ""}`}
                  onClick={() => setProfile(key)}
                  key={key}
                >
                  <strong>{t(key)}</strong>
                  <span>
                    {key === "fast"
                      ? t("profileFastDesc")
                      : key === "standard"
                        ? t("profileStandardDesc")
                        : t("profileDeepDesc")}
                  </span>
                </button>
              ))}
            </div>
            <h3>{t("analysts")}</h3>
            <div className="check-grid">
              {analystKeys.map((key) => (
                <label className="check-card" key={key}>
                  <input
                    type="checkbox"
                    checked={analysts.includes(key)}
                    onChange={() => toggleAnalyst(key)}
                  />
                  <span>
                    <strong>{t(`${key}Analyst`)}</strong>
                    <small>{key}</small>
                  </span>
                </label>
              ))}
            </div>
          </div>
        </article>

        <article className="panel form-section">
          <span className="step">03</span>
          <div className="form-section-body">
            <h2>{t("modelsOutput")}</h2>
            <div className="form-grid three">
              <label>
                {t("provider")}
                <select
                  value={provider}
                  onChange={(event) => changeProvider(event.target.value)}
                >
                  {Object.keys(capabilities?.providers ?? {}).map((key) => (
                    <option key={key} value={key}>
                      {key}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                {t("quickModel")}
                <select
                  value={quickModel}
                  onChange={(event) => {
                    setQuickModel(event.target.value);
                    setQuickReasoning("provider_default");
                  }}
                >
                  {quickOptions.map((option) => (
                    <option key={String(option.value)} value={String(option.value)}>
                      {String(option.label)}
                    </option>
                  ))}
                </select>
                {quickModel === "custom" && (
                  <input
                    required
                    value={quickCustomModel}
                    onChange={(event) => setQuickCustomModel(event.target.value)}
                    placeholder={t("customModel")}
                  />
                )}
              </label>
              <label>
                {t("deepModel")}
                <select
                  value={deepModel}
                  onChange={(event) => {
                    setDeepModel(event.target.value);
                    setDeepReasoning("provider_default");
                  }}
                >
                  {deepOptions.map((option) => (
                    <option key={String(option.value)} value={String(option.value)}>
                      {String(option.label)}
                    </option>
                  ))}
                </select>
                {deepModel === "custom" && (
                  <input
                    required
                    value={deepCustomModel}
                    onChange={(event) => setDeepCustomModel(event.target.value)}
                    placeholder={t("customModel")}
                  />
                )}
              </label>
              <ReasoningSelect
                label={t("quickReasoning")}
                value={quickReasoning}
                options={
                  providerConfig?.reasoning_efforts[
                    quickModel === "custom" ? "custom" : quickModel
                  ] ?? ["provider_default"]
                }
                onChange={setQuickReasoning}
                providerDefault={t("providerDefault")}
              />
              <ReasoningSelect
                label={t("deepReasoning")}
                value={deepReasoning}
                options={
                  providerConfig?.reasoning_efforts[
                    deepModel === "custom" ? "custom" : deepModel
                  ] ?? ["provider_default"]
                }
                onChange={setDeepReasoning}
                providerDefault={t("providerDefault")}
              />
              <label>
                {t("reportLanguage")}
                <select
                  value={outputLanguage}
                  onChange={(event) =>
                    setOutputLanguage(
                      event.target.value as "en" | "zh-CN" | "ja",
                    )
                  }
                >
                  <option value="en">English</option>
                  <option value="zh-CN">
                    Simplified Chinese (简体中文，中国大陆，zh-CN)
                  </option>
                  <option value="ja">日本語</option>
                </select>
              </label>
              <label className="toggle-row">
                <input
                  type="checkbox"
                  checked={provenance}
                  onChange={(event) => setProvenance(event.target.checked)}
                />
                <span>{t("provenance")}</span>
              </label>
            </div>
          </div>
        </article>
        {error && <div className="alert">{error}</div>}
        <div className="form-actions">
          <button
            className="button primary large"
            disabled={submitting || capabilities === null}
          >
            {submitting ? t("loading") : t("startResearch")} →
          </button>
        </div>
      </form>
    </section>
  );
}

function today() {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

function normalizeReportLanguage(value: string): "en" | "zh-CN" | "ja" {
  const normalized = value.trim().toLowerCase();
  if (
    normalized === "zh-cn" ||
    normalized === "zh-hans" ||
    normalized === "chinese" ||
    normalized === "简体中文" ||
    normalized.startsWith("simplified chinese")
  ) {
    return "zh-CN";
  }
  if (normalized === "ja" || normalized.startsWith("japanese")) return "ja";
  return "en";
}

function ReasoningSelect({
  label,
  value,
  options,
  onChange,
  providerDefault,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
  providerDefault: string;
}) {
  return (
    <label>
      {label}
      <select
        value={options.includes(value) ? value : "provider_default"}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {option === "provider_default" ? providerDefault : option}
          </option>
        ))}
      </select>
    </label>
  );
}

function createIdempotencyKey() {
  return globalThis.crypto?.randomUUID?.() ??
    `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
