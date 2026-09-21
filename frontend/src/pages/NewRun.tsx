import RoleConnections from "../components/RoleConnections";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { researchConfidenceLabel } from "../i18n";
import { settingsCopy } from "../settingsCopy";
import {
  api,
  ApiError,
  type AnalysisCutoffContext,
  type AnalysisRequest,
  type Capabilities,
  type FullBaselineCandidate,
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

type AnalysisDateMode = "auto" | "manual";

function reconcileAnalysisDate(
  context: AnalysisCutoffContext,
  mode: AnalysisDateMode,
  manualDate: string,
) {
  if (mode === "manual" && manualDate <= context.max_analysis_date) {
    return {
      analysisDate: manualDate,
      mode,
      manualDate,
      adjusted: false,
    } as const;
  }
  return {
    analysisDate: context.market_date,
    mode: "auto",
    manualDate: "",
    adjusted: mode === "manual",
  } as const;
}

export default function NewRun() {
  const { t, i18n } = useTranslation();
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
  const [ticker, setTicker] = useState("");
  const [analysisDate, setAnalysisDate] = useState("");
  const [analysisContext, setAnalysisContext] =
    useState<AnalysisCutoffContext | null>(null);
  const [analysisContextLoading, setAnalysisContextLoading] = useState(false);
  const [analysisContextError, setAnalysisContextError] = useState("");
  const [analysisDateNotice, setAnalysisDateNotice] = useState("");
  const [analysisContextRefresh, setAnalysisContextRefresh] = useState(0);
  const [profile, setProfile] = useState<"fast" | "standard" | "deep">(
    "standard",
  );
  const [analysts, setAnalysts] = useState<string[]>([...analystKeys]);
  const [quickModel, setQuickModel] = useState("");
  const [deepModel, setDeepModel] = useState("");
  const [quickReasoning, setQuickReasoning] = useState("provider_default");
  const [quickConnection, setQuickConnection] = useState("");
  const [deepConnection, setDeepConnection] = useState("");
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
  const analysisDateMode = useRef<AnalysisDateMode>("auto");
  const manualAnalysisDate = useRef("");
  const applyAnalysisContext = useCallback((
    context: AnalysisCutoffContext,
    selection?: { mode: AnalysisDateMode; manualDate: string },
  ) => {
    const reconciliation = reconcileAnalysisDate(
      context,
      selection?.mode ?? analysisDateMode.current,
      selection?.manualDate ?? manualAnalysisDate.current,
    );
    setAnalysisContext(context);
    analysisDateMode.current = reconciliation.mode;
    manualAnalysisDate.current = reconciliation.manualDate;
    setAnalysisDate(reconciliation.analysisDate);
    return reconciliation;
  }, []);

  useEffect(() => {
    let active = true;
    const instrument = ticker.trim().toUpperCase();
    if (!instrument) {
      setAnalysisContext(null);
      setAnalysisContextLoading(false);
      setAnalysisContextError("");
      setAnalysisDate("");
      return () => {
        active = false;
      };
    }
    setAnalysisContextLoading(true);
    setAnalysisContextError("");
    void api.analysisCutoffContext(instrument)
      .then((context) => {
        if (!active) return;
        const reconciliation = applyAnalysisContext(context);
        if (reconciliation.adjusted) {
          setAnalysisDateNotice(t("analysisDateAdjusted", {
            date: context.market_date,
            timezone: context.market_timezone,
          }));
        }
      })
      .catch(() => {
        if (!active) return;
        setAnalysisContext(null);
        setAnalysisContextError(t("marketDateUnavailable"));
      })
      .finally(() => {
        if (active) setAnalysisContextLoading(false);
      });
    return () => {
      active = false;
    };
  }, [analysisContextRefresh, applyAnalysisContext, t, ticker]);

  useEffect(() => {
    if (!analysisContext) return;
    const observedAt = Date.parse(analysisContext.observed_at);
    const validUntil = Date.parse(analysisContext.valid_until);
    if (!Number.isFinite(observedAt) || !Number.isFinite(validUntil)) return;
    const timer = window.setTimeout(
      () => setAnalysisContextRefresh((value) => value + 1),
      Math.min(2_147_483_647, Math.max(10, validUntil - observedAt)),
    );
    return () => window.clearTimeout(timer);
  }, [analysisContext]);

  useEffect(() => {
    const refreshWhenVisible = () => {
      if (document.visibilityState === "visible" && ticker.trim()) {
        setAnalysisContextRefresh((value) => value + 1);
      }
    };
    document.addEventListener("visibilitychange", refreshWhenVisible);
    return () => document.removeEventListener("visibilitychange", refreshWhenVisible);
  }, [ticker]);

  useEffect(() => {
    let active = true;
    const instrument = analysisContext?.instrument ?? "";
    setFullBaselines([]);
    setPrimaryCycleWarned(false);
    setBaselineEligibilityError("");
    if (!instrument || !analysisDate) return () => {
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
        setResearchKind("full");
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
  }, [analysisContext, analysisDate, entry.baseline, lockedKind, t]);

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
        const sourceRequest = source?.request;
        const sourceIsTerminal = source !== null;
        setProfile(
          (sourceIsTerminal
            ? sourceRequest?.profile
            : data.defaults.profile) as "fast" | "standard" | "deep",
        );
        setTicker(sourceIsTerminal ? (sourceRequest?.ticker ?? "") : "");
        analysisDateMode.current = "auto";
        manualAnalysisDate.current = "";
        setAnalysisDate("");
        setAnalysts(
          sourceIsTerminal
            ? [...(sourceRequest?.analysts ?? analystKeys)]
            : [...(data.defaults.analysts ?? analystKeys)] as typeof analystKeys[number][],
        );
        setOutputLanguage(
          sourceIsTerminal
            ? (sourceRequest?.output_language ?? data.defaults.output_language)
            : data.defaults.output_language,
        );
        setSourceRunId(sourceIsTerminal ? (source?.run_id ?? "") : "");
        if (lockedKind) setResearchKind(lockedKind);
        const q = sourceRequest?.models?.quick ?? data.defaults.models.quick;
        const d = sourceRequest?.models?.deep ?? data.defaults.models.deep;
        setQuickConnection(q?.connection_id ?? ""); setDeepConnection(d?.connection_id ?? "");
        setQuickModel(q?.model ?? ""); setDeepModel(d?.model ?? "");
        setQuickReasoning(q?.reasoning_effort ?? ""); setDeepReasoning(d?.reasoning_effort ?? "");
        if (!data.connections?.[d?.connection_id ?? ""]?.selectable || (lockedKind !== "incremental" && !data.connections?.[q?.connection_id ?? ""]?.selectable)) {
          setTemplateWarning(t("templateConnectionUnavailable"));
        }
        if (!Object.values(data.connections ?? {}).some(connection => connection.selectable)) setError(t("noConfiguredProviders"));
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
    setSubmitting(true);
    setError("");
    let latestContext: AnalysisCutoffContext;
    try {
      latestContext = await api.analysisCutoffContext(ticker.trim());
    } catch {
      setAnalysisContext(null);
      setAnalysisContextError(t("marketDateUnavailable"));
      setAnalysisDate("");
      setSubmitting(false);
      return;
    }
    try {
      const reconciliation = applyAnalysisContext(latestContext);
      if (reconciliation.adjusted) {
        setAnalysisDateNotice(t("analysisDateAdjusted", {
          date: latestContext.market_date,
          timezone: latestContext.market_timezone,
        }));
        setSubmitting(false);
        return;
      }
      const payload: RunCreateRequest = {
        ticker: latestContext.instrument,
        analysis_date: reconciliation.analysisDate,
        profile,
        analysts: analysts as AnalysisRequest["analysts"],
        models: {
          ...(researchKind === "full" ? { quick: { connection_id: quickConnection, model: quickModel.trim(), reasoning_effort: quickReasoning || null } } : {}),
          deep: { connection_id: deepConnection, model: deepModel.trim(), reasoning_effort: deepReasoning || null },
        },
        output_language: outputLanguage,
        research_kind: researchKind,
        full_baseline_run_id:
          researchKind === "incremental" ? fullBaselineRunId || null : null,
        make_primary: researchKind === "full" ? makePrimary : null,
        source_run_id: sourceRunId || null,
      };
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
      if (apiCode === "future_analysis_cutoff" && cause instanceof ApiError && cause.context) {
        applyAnalysisContext(cause.context, {
          mode: "manual",
          manualDate: cause.requestedAnalysisDate ?? analysisDate,
        });
        setAnalysisDateNotice(t("futureAnalysisDate", {
          date: cause.requestedAnalysisDate ?? analysisDate,
          marketDate: cause.context.market_date,
          timezone: cause.context.market_timezone,
        }));
      } else if (apiCode === "unsupported_instrument") {
        setError(t("unsupportedInstrument"));
      } else if (apiCode === "instrument_eligibility_unavailable") {
        setError(t("eligibilityUnavailable"));
      } else {
        setError(cause instanceof Error ? cause.message : t("error"));
      }
      setSubmitting(false);
    }
  };

  const unavailableConnection = Object.keys(capabilities?.connections ?? {}).length > 0 && (!capabilities?.connections?.[deepConnection]?.selectable || (researchKind === "full" && !capabilities?.connections?.[quickConnection]?.selectable));
  const submitUnavailable = submitting ? t("loading")
    : capabilities?.configuration_initialized === false ? settingsCopy[i18n.language.startsWith("zh") ? "zh-CN" : i18n.language.startsWith("ja") ? "ja" : "en"].setup
    : !ticker.trim() ? t("enterInstrumentFirst")
    : analysisContextLoading ? t("marketDateLoading")
    : !analysisContext ? analysisContextError || t("marketDateLoading")
    : !analysisDate ? t("selectAnalysisDate")
    : !capabilities ? t("loading")
    : unavailableConnection ? t("chooseResearchModels")
    : !deepConnection || !deepModel || researchKind === "full" && (!quickConnection || !quickModel) ? t("chooseResearchModels")
    : researchKind === "incremental" && !fullBaselineRunId ? t("selectResearchBaseline")
    : "";

  return (
    <section>
      {capabilities?.configuration_initialized === false && <div className="alert"><Link to="/settings">{settingsCopy[i18n.language.startsWith("zh") ? "zh-CN" : i18n.language.startsWith("ja") ? "ja" : "en"].setup}</Link></div>}
      <header className="page-header">
        <div>
          <h1>{t("newRun")}</h1>
          <p className="subtitle">{t("newResearchHint")}</p>
        </div>
      </header>
      {sourceRunId && (
        <div className="panel template-source">
          {t("templateFromRun")}{" "}
          <Link to={`/runs/${encodeURIComponent(sourceRunId)}`}>
            {t("executionDetails")}
          </Link>
        </div>
      )}
      {templateWarning && <div className="alert">{templateWarning}</div>}
      <form className="run-form" onSubmit={submit} onInvalidCapture={event => { const details = (event.target as HTMLElement).closest("details"); if (details) details.open = true; }}>
        <article className="panel form-section">
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
                  disabled={submitting}
                  value={ticker}
                  onChange={(event) => {
                    setTicker(event.target.value);
                    setAnalysisContext(null);
                    setAnalysisContextError("");
                    setAnalysisDateNotice("");
                    setAnalysisDate("");
                  }}
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
                  disabled={submitting || !analysisContext || analysisContextLoading}
                  max={analysisContext?.max_analysis_date}
                  value={analysisDate}
                  onChange={(event) => {
                    analysisDateMode.current = "manual";
                    manualAnalysisDate.current = event.target.value;
                    setAnalysisDate(event.target.value);
                    setAnalysisDateNotice("");
                  }}
                />
                <small>
                  {analysisContext
                    ? t("marketDateContext", {
                        date: analysisContext.market_date,
                        timezone: analysisContext.market_timezone,
                      })
                    : t("cutoffHint")}
                </small>
                {analysisContextError && <small id="cutoff-context-error" className="warning" role="alert">{analysisContextError} <button type="button" className="text-button" disabled={submitting} onClick={() => setAnalysisContextRefresh(value => value + 1)}>{t("retryLoad")}</button></small>}
                {analysisDateNotice && <small className="warning" role="status">{analysisDateNotice}</small>}
              </label>
            </div>
          </div>
        </article>

        <article className="panel form-section">
          <div className="form-section-body">
            <h2>{t("researchConfiguration")}</h2>
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
        {researchKind === "full" && <div className="primary-cycle-choice">
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
                          : ` · ${researchConfidenceLabel(t, baseline.confidence)}`}
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

          <div className="form-section-body">
            {researchKind === "full" && (
              <>
                <h3>{t("profile")}</h3>
                <div className="profile-grid">
                  {(["fast", "standard", "deep"] as const).map((key) => (
                    <button
                      type="button"
                      aria-pressed={profile === key}
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
        <details className="advanced-configuration">
          <summary>{t("advancedConfiguration")}</summary>
          <div className="form-section-body">
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
                  </span>
                </label>
              ))}
            </div>
            <h2>{t("modelsOutput")}</h2>
            <RoleConnections language={i18n.language} connections={capabilities?.connections ?? {}} includeQuick={researchKind === "full"}
              value={{ quick: { connection: quickConnection, model: quickModel, reasoning: quickReasoning }, deep: { connection: deepConnection, model: deepModel, reasoning: deepReasoning } }}
              onChange={roles => { setQuickConnection(roles.quick.connection); setDeepConnection(roles.deep.connection); setQuickModel(roles.quick.model); setDeepModel(roles.deep.model); setQuickReasoning(roles.quick.reasoning); setDeepReasoning(roles.deep.reasoning); }} />
          </div>
        </details>
        </article>
        <section className="request-summary panel" aria-label={t("requestSummary")}>
          <h2>{t("requestSummary")}</h2>
          <dl className="definition-list">
            <div><dt>{t("ticker")}</dt><dd>{ticker || "—"}</dd></div>
            <div><dt>{t("analysisDate")}</dt><dd>{analysisDate || "—"}</dd></div>
            <div><dt>{t("researchKind")}</dt><dd>{t(researchKind === "full" ? "newCycle" : "incrementalResearch")}{researchKind === "full" ? ` · ${t(profile)}` : ` · ${t("baselineDate")}: ${fullBaselines.find(item => item.id === fullBaselineRunId)?.analysis_date ?? "—"}`}</dd></div>
            <div><dt>{t("analysts")}</dt><dd>{analysts.map(key => t(`${key}Analyst`)).join(" · ") || "—"}</dd></div>
            <div><dt>{t("reportLanguage")}</dt><dd>{reportLanguageLabel(outputLanguage)}</dd></div>
            {researchKind === "full" && <div><dt>{t("makePrimary")}</dt><dd>{t(makePrimary ? "enabled" : "disabled")}</dd></div>}
          </dl>
        </section>
        {error && <div className="alert">{error}</div>}
        {submitUnavailable && !analysisContextError && <p id="submit-unavailable" role="status">{submitUnavailable}</p>}
        <div className="form-actions">
          <button
            className="button primary large"
            disabled={Boolean(submitUnavailable)}
            aria-describedby={analysisContextError ? "cutoff-context-error" : submitUnavailable ? "submit-unavailable" : undefined}
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
            : researchConfidenceLabel(t, baseline.confidence)}
        </span>
      </div>
      {baseline.thesis && <p>{baseline.thesis}</p>}
    </article>
  );
}

function reportLanguageLabel(value: string) {
  if (value === "en") return "English";
  if (value === "zh-CN") return "简体中文";
  if (value === "ja") return "日本語";
  return value;
}

function createIdempotencyKey() {
  return globalThis.crypto?.randomUUID?.() ??
    `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
