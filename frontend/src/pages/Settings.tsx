import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import i18n from "../i18n";
import { api, type Capabilities } from "../api/client";

const defaultLabelKeys: Record<keyof Capabilities["defaults"], string> = {
  profile: "defaultProfile",
  llm_provider: "defaultProvider",
  quick_model: "quickModel",
  deep_model: "deepModel",
  quick_reasoning_effort: "quickReasoning",
  deep_reasoning_effort: "deepReasoning",
  output_language: "reportLanguage",
  lan_enabled: "lanAccess",
  trash_retention_days: "trashRetentionDays",
};

export default function Settings() {
  const { t } = useTranslation();
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem("tradingagents-sidebar-collapsed") === "true");
  useEffect(() => {
    let active = true;
    setError("");
    void api.capabilities().then(value => { if (active) setCapabilities(value); }, cause => { if (active) setError(String(cause)); });
    return () => { active = false; };
  }, [revision]);
  return (
    <section>
      <header className="page-header">
        <div>
          <h1>{t("settings")}</h1>
          <p className="subtitle">{t("secretsHint")}</p>
        </div>
      </header>
      <section className="interface-preferences">
        <h2>{t("interfacePreferences")}</h2>
        <label>{t("language")}<select value={i18n.language} onChange={event => { localStorage.setItem("tradingagents-locale", event.target.value); void i18n.changeLanguage(event.target.value); }}><option value="zh-CN">简体中文</option><option value="en">English</option><option value="ja">日本語</option></select></label>
        <label className="checkbox-label"><input type="checkbox" checked={collapsed} onChange={event => { setCollapsed(event.target.checked); localStorage.setItem("tradingagents-sidebar-collapsed", String(event.target.checked)); window.dispatchEvent(new Event("tradingagents:preferences")); }} />{t("collapseSidebar")}</label>
      </section>
      <h2>{t("readonlyConfig")}</h2>
      {error && <div className="alert" role="alert">{error}<button className="button" onClick={() => setRevision(value => value + 1)}>{t("retryLoad")}</button></div>}
      {!capabilities && !error && <div className="loading">{t("loading")}</div>}
      {capabilities && <div className="settings-grid">
        <article className="panel">
          <div className="panel-header">
            <h2>{t("defaults")}</h2>
          </div>
          <dl className="definition-list">
            {(
              Object.entries(capabilities.defaults) as [
                keyof Capabilities["defaults"],
                Capabilities["defaults"][keyof Capabilities["defaults"]],
              ][]
            ).map(([key, value]) => (
              <div key={key}>
                <dt>{t(defaultLabelKeys[key])}</dt>
                <dd>
                  {value === null
                    ? t("providerDefault")
                    : typeof value === "boolean"
                      ? t(value ? "enabled" : "disabled")
                      : key === "profile" ? t(String(value))
                      : key === "llm_provider" ? capabilities.providers[String(value)]?.label ?? String(value)
                      : value === "provider_default" ? t("providerDefault")
                      : key === "output_language" ? ({ en: "English", "zh-CN": "简体中文", zh: "简体中文", ja: "日本語" }[String(value)] ?? String(value))
                      : String(value)}
                </dd>
              </div>
            ))}
          </dl>
        </article>
        <article className="panel">
          <div className="panel-header">
            <h2>{t("apiKeys")}</h2>
          </div>
          <div className="provider-list">
            {Object.entries(capabilities.providers).map(([name, config]) => (
              <div key={name}>
                <strong>
                  {config.label}
                  <small>{name}</small>
                </strong>
                <span
                  className={
                    config.configured ? "configured" : "missing"
                  }
                >
                  {config.configured
                    ? config.api_key_required
                      ? t("configured")
                      : t("ready")
                    : t("missing")}
                </span>
              </div>
            ))}
          </div>
        </article>
      </div>}
    </section>
  );
}
