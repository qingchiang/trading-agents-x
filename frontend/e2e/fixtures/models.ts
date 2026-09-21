import configuration from "./configuration.json" with { type: "json" };

export const models = configuration.view.values.models;

export function capabilities(outputLanguage = "en") {
  return {
    configuration_initialized: true,
    connections: configuration.view.connections,
    profiles: ["fast", "standard", "deep"],
    analysts: ["market", "social", "news", "fundamentals"],
    output_languages: ["en", "zh-CN", "ja"],
    providers: {},
    defaults: {
      models,
      profile: "standard",
      output_language: outputLanguage,
      analysts: ["market", "social", "news", "fundamentals"],
      lan_enabled: false,
      trash_retention_days: 30,
    },
  };
}

export const modelCatalog = {
  provider: "openai",
  models: [
    { id: models.quick.model, label: "Quick", compatibility: "supported", reasoning_efforts: ["provider_default", "low"], default_roles: ["quick"] },
    { id: models.deep.model, label: "Deep", compatibility: "supported", reasoning_efforts: ["provider_default", "high"], default_roles: ["deep"] },
  ],
  source: "fixture",
  fetched_at: "2026-07-24T00:00:00Z",
  stale: false,
  warning: null,
};
