import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
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
  useEffect(() => {
    void api.capabilities().then(setCapabilities);
  }, []);

  if (!capabilities) return <div className="loading">{t("loading")}</div>;
  return (
    <section>
      <header className="page-header">
        <div>
          <p className="eyebrow">{t("readonlyConfig")}</p>
          <h1>{t("settings")}</h1>
          <p className="subtitle">{t("secretsHint")}</p>
        </div>
      </header>
      <div className="settings-grid">
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
      </div>
    </section>
  );
}
