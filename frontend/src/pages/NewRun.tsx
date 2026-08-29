import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  api,
  type AnalysisRequest,
  type Capabilities,
  type DiscoveredModel,
  type FullBaselineCandidate,
  type ProviderModelCatalog,
  type RunCreateRequest,
} from "../api/client";
import {
  InstrumentIdentity,
  RecentInstrumentDatalist,
  recentInstrumentListId,
  useRecentInstruments,
} from "../components/Instruments";
import { Link, useLocation, useNavigate } from "../router";

const analystKeys = ["market", "social", "news", "fundamentals"] as const;
const customModelValue = "__custom_model_id__";
export default function NewRun() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const location = useLocation();
  const entry = useMemo(() => {
    const params = new URLSearchParams(location.search);
    return {
      fromRun: params.get("from_run")?.trim() ?? "",
      intent: params.get("intent")?.trim() ?? "",
      baseline: params.get("full_baseline_run_id")?.trim() ?? "",
    };
  }, [location.search]);
  const fromRun = entry.fromRun;
  const lockedKind =
    entry.intent === "update"
      ? "incremental"
      : entry.intent === "clone_full"
        ? "full"
        : null;
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
  const [outputLanguage, setOutputLanguage] = useState("en");
  const [sourceRunId, setSourceRunId] = useState("");
  const [makePrimary, setMakePrimary] = useState(true);
  const [researchKind, setResearchKind] = useState<"full" | "incremental">(
    "full",
  );
  const [fullBaselines, setFullBaselines] = useState<FullBaselineCandidate[]>([]);
  const [fullBaselineRunId, setFullBaselineRunId] = useState("");
  const [primaryCycleWarned, setPrimaryCycleWarned] = useState(false);
  const [baselineEligibilityError, setBaselineEligibilityError] = useState("");
  const [templateWarning, setTemplateWarning] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const recentInstruments = useRecentInstruments();
  const submission = useRef<{ fingerprint: string; key: string } | null>(null);
  const researchKindSelectedByUser = useRef(false);
  const baselineSelectedByUser = useRef(false);

  useEffect(() => {
    let active = true;
    const instrument = ticker.trim().toUpperCase();
    setFullBaselines([]);
    setPrimaryCycleWarned(false);
    setBaselineEligibilityError("");
    if (!instrument) return () => {
      active = false;
    };
    const loadBaselines = async () => {
      const response = await api.baselineCandidates(instrument, analysisDate);
      if (!active) return;
      const baselines = response.items ?? [];
      const primaryWarning = baselines.find((item) => item.is_primary)?.cycle_warning ?? false;
      const requestedBaselineEligible = Boolean(
        entry.baseline && baselines.some((item) => item.id === entry.baseline),
      );
      setFullBaselines(baselines);
      setPrimaryCycleWarned(primaryWarning);
      setBaselineEligibilityError(
        lockedKind === "incremental" && !requestedBaselineEligible
          ? t("requestedBaselineUnavailable")
          : "",
      );
      setFullBaselineRunId((current) => {
        const requested = entry.baseline;
        if (lockedKind === "incremental") {
          return requestedBaselineEligible ? requested : "";
        }
        if (requestedBaselineEligible) return requested;
        if (baselineSelectedByUser.current && baselines.some((item) => item.id === current)) {
          return current;
        }
        return baselines[0]?.id ?? "";
      });
      if (lockedKind) {
        setResearchKind(lockedKind);
      } else if (!researchKindSelectedByUser.current) {
        setResearchKind(baselines.length > 0 && !primaryWarning ? "incremental" : "full");
      }
    };
    void loadBaselines()
      .catch(() => {
        if (active) {
          setFullBaselines([]);
          setFullBaselineRunId("");
          if (lockedKind === "incremental") {
            setBaselineEligibilityError(t("requestedBaselineUnavailable"));
          }
        }
      });
    return () => {
      active = false;
    };
  }, [analysisDate, entry.baseline, lockedKind, t, ticker]);

  useEffect(() => {
    let active = true;
    setTemplateWarning("");
    setSourceRunId("");
    const bootstrap = async () => {
      try {
        const [data, source] = await Promise.all([
          api.capabilities(),
          fromRun
            ? api.creationTemplate(fromRun).catch(() => {
                if (active) setTemplateWarning(t("templateLoadFailed", { id: fromRun }));
                return null;
              })
            : Promise.resolve(null),
        ]);
        if (!active) return;
        setCapabilities(data);
        const selectableProviders = Object.entries(data.providers).filter(
          ([, config]) => config.selectable,
        );
        const defaultProvider = data.providers[data.defaults.llm_provider]?.selectable
          ? data.defaults.llm_provider
          : (selectableProviders[0]?.[0] ?? "");
        const sourceRequest = source?.request;
        const sourceIsTerminal = source !== null;
        const sourceProvider =
          sourceIsTerminal ? (sourceRequest?.llm_provider ?? "") : "";
        const sourceProviderAvailable =
          Boolean(sourceProvider) &&
          Boolean(data.providers[sourceProvider]?.selectable);
        const nextProvider = sourceProviderAvailable
          ? sourceProvider
          : defaultProvider;
        setProfile(
          (sourceIsTerminal
            ? sourceRequest?.profile
            : data.defaults.profile) as "fast" | "standard" | "deep",
        );
        setTicker(sourceIsTerminal ? (sourceRequest?.ticker ?? "") : "");
        setAnalysisDate(today());
        setAnalysts(
          sourceIsTerminal
            ? [...(sourceRequest?.analysts ?? analystKeys)]
            : [...analystKeys],
        );
        setProvider(nextProvider);
        if (sourceIsTerminal && sourceProviderAvailable) {
          setQuickModel(sourceRequest?.quick_model ?? "");
          setDeepModel(sourceRequest?.deep_model ?? "");
          setQuickReasoning(
            sourceRequest?.quick_reasoning_effort ?? "provider_default",
          );
          setDeepReasoning(
            sourceRequest?.deep_reasoning_effort ?? "provider_default",
          );
        } else if (nextProvider === data.defaults.llm_provider) {
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
        setOutputLanguage(
          sourceIsTerminal
            ? (sourceRequest?.output_language ?? data.defaults.output_language)
            : data.defaults.output_language,
        );
        setSourceRunId(sourceIsTerminal ? (source?.run_id ?? "") : "");
        if (lockedKind) setResearchKind(lockedKind);
        if (
          sourceIsTerminal &&
          sourceProvider &&
          !sourceProviderAvailable
        ) {
          setTemplateWarning(
            t("templateProviderUnavailable", {
              provider: sourceProvider,
            }),
          );
        } else if (sourceIsTerminal) {
          setTemplateWarning("");
        }
        if (!nextProvider) setError(t("noConfiguredProviders"));
      } catch (cause) {
        if (!active) return;
        setError(cause instanceof Error ? cause.message : t("error"));
      }
    };
    void bootstrap();
    return () => {
      active = false;
    };
  }, [fromRun, lockedKind, t]);

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
          reasoningOptions(catalog, selectedQuick, current).includes(current)
            ? current
            : "provider_default",
        );
        setDeepReasoning((current) =>
          reasoningOptions(catalog, selectedDeep, current).includes(current)
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
        if (!sourceRunId) {
          setQuickReasoning("provider_default");
          setDeepReasoning("provider_default");
        }
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
  const reportLanguageOptions = capabilities?.output_languages ?? [
    "en",
    "zh-CN",
    "ja",
  ];
  const configuredOutputLanguage = capabilities?.defaults.output_language ?? "";
  const customOutputLanguage =
    outputLanguage && !reportLanguageOptions.includes(outputLanguage)
      ? outputLanguage
      : "";

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
      (researchKind === "full" &&
        quickModel === customModelValue &&
        !quickCustomModel.trim()) ||
      (deepModel === customModelValue && !deepCustomModel.trim())
    ) {
      setError(t("customModel"));
      return;
    }
    setSubmitting(true);
    setError("");
    const resolvedQuickModel = quickModel === customModelValue
      ? quickCustomModel.trim()
      : quickModel;
    const resolvedDeepModel =
      deepModel === customModelValue ? deepCustomModel.trim() : deepModel;
    const payload: RunCreateRequest = {
      ticker,
      analysis_date: analysisDate,
      asset_type: "stock",
      profile,
      analysts: analysts as AnalysisRequest["analysts"],
      llm_provider: provider,
      quick_model: resolvedQuickModel,
      deep_model: resolvedDeepModel,
      quick_reasoning_effort: quickReasoning,
      deep_reasoning_effort: deepReasoning,
      output_language: outputLanguage,
      research_kind: researchKind,
      full_baseline_run_id:
        researchKind === "incremental" ? fullBaselineRunId || null : null,
      make_primary: researchKind === "full" ? makePrimary : null,
      source_run_id: sourceRunId || null,
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
      const apiCode =
        typeof cause === "object" && cause !== null && "code" in cause
          ? (cause as { code?: unknown }).code
          : undefined;
      if (apiCode === "unsupported_instrument") {
        setError(t("unsupportedInstrument"));
      } else if (apiCode === "instrument_eligibility_unavailable") {
        setError(t("eligibilityUnavailable"));
      } else {
        setError(cause instanceof Error ? cause.message : t("error"));
      }
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
      {sourceRunId && (
        <div className="panel template-source">
          {t("templateFromRun")}{" "}
          <Link to={`/runs/${encodeURIComponent(sourceRunId)}`}>
            {sourceRunId}
          </Link>
        </div>
      )}
      {templateWarning && <div className="alert">{templateWarning}</div>}
      <form className="run-form" onSubmit={submit}>
        <article className="panel form-section">
          <span className="step">01</span>
          <div className="form-section-body">
            <h2>{t("instrumentCutoff")}</h2>
            <div className="form-grid two">
              <label>
                {t("ticker")}
                <input
                  id="new-run-ticker"
                  name="ticker"
                  required
                  autoFocus
                  autoComplete="on"
                  list={recentInstrumentListId}
                  spellCheck={false}
                  value={ticker}
                  onChange={(event) => setTicker(event.target.value)}
                  placeholder="7203.T"
                />
                <RecentInstrumentDatalist instruments={recentInstruments} />
                <small>{t("tickerHint")}</small>
              </label>
              <label>
                {t("analysisDate")}
                <input
                  id="new-run-analysis-date"
                  name="analysis_date"
                  required
                  type="date"
                  autoComplete="off"
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
            <h2>{t("researchKind")}</h2>
            {lockedKind ? (
              <div className={`research-kind-lock ${lockedKind}`}>
                <strong>
                  {t(lockedKind === "full" ? "fullResearch" : "incrementalResearch")}
                </strong>
                <span>
                  {t(lockedKind === "full" ? "cloneFullIntentHint" : "updateResearchIntentHint")}
                </span>
              </div>
            ) : <div className="check-grid">
              <label className="check-card">
                <input
                  type="radio"
                  name="research-kind"
                  checked={researchKind === "full"}
                  onChange={() => {
                    researchKindSelectedByUser.current = true;
                    setResearchKind("full");
                  }}
                />
                <span>
                  <strong>{t("fullResearch")}</strong>
                  <small>{t("fullResearchHint")}</small>
                </span>
              </label>
              <label className="check-card">
                <input
                  type="radio"
                  name="research-kind"
                  checked={researchKind === "incremental"}
                  disabled={!fullBaselines.length}
                  onChange={() => {
                    researchKindSelectedByUser.current = true;
                    setResearchKind("incremental");
                  }}
                />
                <span>
                  <strong>{t("incrementalResearch")}</strong>
                  <small>{t("incrementalResearchHint")}</small>
                </span>
              </label>
            </div>}
            {researchKind === "incremental" && (
              <div className="baseline-picker">
                <label>
                  {t("fullBaseline")}
                  <select
                    value={fullBaselineRunId}
                    disabled={lockedKind === "incremental"}
                    onChange={(event) => {
                      baselineSelectedByUser.current = true;
                      setFullBaselineRunId(event.target.value);
                    }}
                    required
                  >
                    {!fullBaselineRunId && (
                      <option value="">{t("selectFullBaseline")}</option>
                    )}
                    {fullBaselines.map((baseline) => (
                      <option key={baseline.id} value={baseline.id}>
                        {baseline.is_primary ? `${t("primaryCycle")} · ` : ""}
                        {baseline.analysis_date} · {baseline.rating ?? t("notRecorded")}
                        {baseline.confidence == null
                          ? ""
                          : ` · ${Math.round(baseline.confidence * 100)}%`}
                      </option>
                    ))}
                  </select>
                </label>
                {baselineEligibilityError && (
                  <div className="alert" role="alert">
                    {baselineEligibilityError}
                  </div>
                )}
                {fullBaselines.find((item) => item.id === fullBaselineRunId) && (
                  <BaselinePreview
                    baseline={fullBaselines.find((item) => item.id === fullBaselineRunId)!}
                    ticker={ticker.trim().toUpperCase()}
                  />
                )}
              </div>
            )}
            {fullBaselines.length > 0 && (
              <p className="model-catalog-note">
                {t(
                  primaryCycleWarned
                    ? "fullResearchRecommendedForWarning"
                    : "incrementalAvailable",
                )}
              </p>
            )}
          </div>
        </article>

        <article className="panel form-section">
          <span className="step">03</span>
          <div className="form-section-body">
            {researchKind === "full" && (
              <>
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
              </>
            )}
            <h2>{t(researchKind === "incremental" ? "updateScope" : "analysts")}</h2>
            {researchKind === "incremental" && (
              <p className="section-hint">{t("updateScopeHint")}</p>
            )}
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
          <span className="step">04</span>
          <div className="form-section-body">
            <h2>{t("modelsOutput")}</h2>
            <div className={`form-grid ${researchKind === "full" ? "three" : "two"}`}>
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
              {researchKind === "full" && (
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
              )}
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
              {researchKind === "full" && (
                <ReasoningSelect
                  label={t("quickReasoning")}
                  value={quickReasoning}
                  options={reasoningOptions(
                    modelCatalog,
                    quickModel,
                    quickReasoning,
                  )}
                  onChange={setQuickReasoning}
                  providerDefault={t("providerDefault")}
                />
              )}
              <ReasoningSelect
                label={t("deepReasoning")}
                value={deepReasoning}
                options={reasoningOptions(
                  modelCatalog,
                  deepModel,
                  deepReasoning,
                )}
                onChange={setDeepReasoning}
                providerDefault={t("providerDefault")}
              />
              <label>
                {t("reportLanguage")}
                <select
                  value={outputLanguage}
                  onChange={(event) => setOutputLanguage(event.target.value)}
                >
                  {customOutputLanguage && (
                    <option value={customOutputLanguage}>
                      {customOutputLanguage === configuredOutputLanguage
                        ? t("configuredOutputLanguage", {
                            value: customOutputLanguage,
                          })
                        : t("sourceOutputLanguage", {
                            value: customOutputLanguage,
                          })}
                    </option>
                  )}
                  {reportLanguageOptions.map((language) => (
                    <option key={language} value={language}>
                      {reportLanguageLabel(language)}
                    </option>
                  ))}
                </select>
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
        {researchKind === "full" && <article className="panel form-section">
          <span className="step">05</span>
          <div className="form-section-body">
            <h2>{t("primaryResearch")}</h2>
            <label className="check-card">
              <input
                type="checkbox"
                checked={makePrimary}
                onChange={(event) => setMakePrimary(event.target.checked)}
              />
              <span>
                <strong>{t("makePrimary")}</strong>
                <small>{t("makePrimaryHint")}</small>
              </span>
            </label>
          </div>
        </article>}
        {error && <div className="alert">{error}</div>}
        <div className="form-actions">
          <button
            className="button primary large"
            disabled={
              submitting ||
              capabilities === null ||
              modelsLoading ||
              !provider ||
              (researchKind === "full" && !quickModel) ||
              !deepModel ||
              (researchKind === "incremental" && !fullBaselineRunId)
            }
          >
            {submitting ? t("loading") : t("startResearch")} →
          </button>
        </div>
      </form>
    </section>
  );
}

function BaselinePreview({
  baseline,
  ticker,
}: {
  baseline: FullBaselineCandidate;
  ticker: string;
}) {
  const { t } = useTranslation();
  return (
    <article className="baseline-preview">
      <InstrumentIdentity
        ticker={ticker}
        instrumentName={baseline.instrument_name}
        instrumentLocalName={baseline.instrument_local_name}
      />
      <div className="baseline-decision">
        <strong>{baseline.rating ?? t("notRecorded")}</strong>
        <span>
          {baseline.confidence == null
            ? t("notRecorded")
            : t("confidencePercent", { value: Math.round(baseline.confidence * 100) })}
        </span>
      </div>
      {baseline.thesis && <p>{baseline.thesis}</p>}
    </article>
  );
}

function today() {
  const now = new Date();
  const local = new Date(now.getTime() - now.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

function reportLanguageLabel(value: string) {
  if (value === "en") return "English";
  if (value === "zh-CN") return "简体中文";
  if (value === "ja") return "日本語";
  return value;
}

type ModelRole = "quick" | "deep";

function chooseModel(
  catalog: ProviderModelCatalog,
  current: string,
  configuredDefault: string,
  role: ModelRole,
) {
  const ids = new Set(catalog.models.map((model) => model.id));
  if (current && current !== customModelValue) return current;
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
  current = "",
) {
  const options =
    !model || model === customModelValue
      ? ["provider_default"]
      : (catalog?.models.find((option) => option.id === model)
          ?.reasoning_efforts ?? ["provider_default"]);
  return current && !options.includes(current)
    ? [...options, current]
    : options;
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
