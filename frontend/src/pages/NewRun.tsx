import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  api,
  type AnalysisRequest,
  type Capabilities,
  type DiscoveredModel,
  type ProviderModelCatalog,
} from "../api/client";
import { useNavigate } from "../router";

const analystKeys = ["market", "social", "news", "fundamentals"] as const;
const customModelValue = "__custom_model_id__";

export default function NewRun() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [modelCatalog, setModelCatalog] =
    useState<ProviderModelCatalog | null>(null);
  const [modelsLoading, setModelsLoading] = useState(false);
  const [modelWarning, setModelWarning] = useState("");
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
        const selectableProviders = Object.entries(data.providers).filter(
          ([, config]) => config.selectable,
        );
        const nextProvider = data.providers[data.defaults.llm_provider]?.selectable
          ? data.defaults.llm_provider
          : (selectableProviders[0]?.[0] ?? "");
        setProfile(
          data.defaults.profile as "fast" | "standard" | "deep",
        );
        setProvider(nextProvider);
        if (nextProvider === data.defaults.llm_provider) {
          setQuickModel(data.defaults.quick_model);
          setDeepModel(data.defaults.deep_model);
          setQuickReasoning(
            data.defaults.quick_reasoning_effort ?? "provider_default",
          );
          setDeepReasoning(
            data.defaults.deep_reasoning_effort ?? "provider_default",
          );
        } else {
          setQuickModel("");
          setDeepModel("");
          setQuickReasoning("provider_default");
          setDeepReasoning("provider_default");
        }
        setOutputLanguage(normalizeReportLanguage(data.defaults.output_language));
        setProvenance(data.defaults.provenance);
        if (!nextProvider) setError(t("noConfiguredProviders"));
      })
      .catch((cause) => {
        setError(cause instanceof Error ? cause.message : t("error"));
      });
  }, [t]);

  useEffect(() => {
    if (!capabilities || !provider) return;
    let active = true;
    setModelsLoading(true);
    setModelCatalog(null);
    setModelWarning("");
    void api
      .providerModels(provider)
      .then((catalog) => {
        if (!active) return;
        setModelCatalog(catalog);
        setModelWarning(catalog.warning?.message ?? "");
        const isDefaultProvider =
          provider === capabilities.defaults.llm_provider;
        const selectedQuick = chooseModel(
          catalog,
          quickModel,
          isDefaultProvider ? capabilities.defaults.quick_model : "",
          "quick",
        );
        const selectedDeep = chooseModel(
          catalog,
          deepModel,
          isDefaultProvider ? capabilities.defaults.deep_model : "",
          "deep",
        );
        setQuickModel(selectedQuick);
        setDeepModel(selectedDeep);
        setQuickReasoning((current) =>
          reasoningOptions(catalog, selectedQuick).includes(current)
            ? current
            : "provider_default",
        );
        setDeepReasoning((current) =>
          reasoningOptions(catalog, selectedDeep).includes(current)
            ? current
            : "provider_default",
        );
      })
      .catch((cause) => {
        if (!active) return;
        setModelWarning(
          cause instanceof Error ? cause.message : "Model discovery failed",
        );
        const isDefaultProvider =
          provider === capabilities.defaults.llm_provider;
        setQuickModel((current) =>
          current ||
          (isDefaultProvider
            ? capabilities.defaults.quick_model
            : customModelValue),
        );
        setDeepModel((current) =>
          current ||
          (isDefaultProvider
            ? capabilities.defaults.deep_model
            : customModelValue),
        );
        setQuickReasoning("provider_default");
        setDeepReasoning("provider_default");
      })
      .finally(() => {
        if (active) setModelsLoading(false);
      });
    return () => {
      active = false;
    };
  }, [capabilities, provider]);

  const selectableProviders = useMemo(
    () =>
      Object.entries(capabilities?.providers ?? {}).filter(
        ([, config]) => config.selectable,
      ),
    [capabilities],
  );
  const quickOptions = useMemo(
    () => modelOptions(modelCatalog, quickModel),
    [modelCatalog, quickModel],
  );
  const deepOptions = useMemo(
    () => modelOptions(modelCatalog, deepModel),
    [modelCatalog, deepModel],
  );

  const changeProvider = (next: string) => {
    setProvider(next);
    setModelCatalog(null);
    setModelWarning("");
    setQuickModel("");
    setDeepModel("");
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
      (quickModel === customModelValue && !quickCustomModel.trim()) ||
      (deepModel === customModelValue && !deepCustomModel.trim())
    ) {
      setError(t("customModel"));
      return;
    }
    setSubmitting(true);
    setError("");
    const resolvedQuickModel =
      quickModel === customModelValue ? quickCustomModel.trim() : quickModel;
    const resolvedDeepModel =
      deepModel === customModelValue ? deepCustomModel.trim() : deepModel;
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
                  {selectableProviders.map(([key, config]) => (
                    <option key={key} value={key}>
                      {config.label}
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
                    <option key={option.id} value={option.id}>
                      {option.id === customModelValue
                        ? t("customModel")
                        : option.label}
                    </option>
                  ))}
                </select>
                {quickModel === customModelValue && (
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
                    <option key={option.id} value={option.id}>
                      {option.id === customModelValue
                        ? t("customModel")
                        : option.label}
                    </option>
                  ))}
                </select>
                {deepModel === customModelValue && (
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
                options={reasoningOptions(modelCatalog, quickModel)}
                onChange={setQuickReasoning}
                providerDefault={t("providerDefault")}
              />
              <ReasoningSelect
                label={t("deepReasoning")}
                value={deepReasoning}
                options={reasoningOptions(modelCatalog, deepModel)}
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
            {(modelsLoading || modelWarning) && (
              <p
                className={`model-catalog-note ${
                  modelWarning ? "warning" : ""
                }`}
              >
                {modelsLoading ? t("discoveringModels") : modelWarning}
              </p>
            )}
          </div>
        </article>
        {error && <div className="alert">{error}</div>}
        <div className="form-actions">
          <button
            className="button primary large"
            disabled={
              submitting ||
              capabilities === null ||
              modelsLoading ||
              !provider ||
              !quickModel ||
              !deepModel
            }
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

type ModelRole = "quick" | "deep";

function chooseModel(
  catalog: ProviderModelCatalog,
  current: string,
  configuredDefault: string,
  role: ModelRole,
) {
  const ids = new Set(catalog.models.map((model) => model.id));
  if (current && current !== customModelValue && ids.has(current)) return current;
  if (configuredDefault && ids.has(configuredDefault)) return configuredDefault;
  return (
    catalog.models.find((model) => model.default_roles.includes(role))?.id ??
    catalog.models[0]?.id ??
    customModelValue
  );
}

function modelOptions(
  catalog: ProviderModelCatalog | null,
  current: string,
): DiscoveredModel[] {
  const options = [...(catalog?.models ?? [])];
  if (
    current &&
    current !== customModelValue &&
    !options.some((model) => model.id === current)
  ) {
    options.unshift({
      id: current,
      label: current,
      compatibility: "unknown",
      reasoning_efforts: ["provider_default"],
      default_roles: [],
    });
  }
  options.push({
    id: customModelValue,
    label: "Custom model ID",
    compatibility: "unknown",
    reasoning_efforts: ["provider_default"],
    default_roles: [],
  });
  return options;
}

function reasoningOptions(
  catalog: ProviderModelCatalog | null,
  model: string,
) {
  if (!model || model === customModelValue) return ["provider_default"];
  return (
    catalog?.models.find((option) => option.id === model)?.reasoning_efforts ??
    ["provider_default"]
  );
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
