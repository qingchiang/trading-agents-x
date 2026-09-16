import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import i18n from "../i18n";
import {
  api,
  ApiError,
  type Capabilities,
  type ConfigurationField,
  type ConfigurationSchema,
  type ConfigurationValues,
  type ConfigurationView,
  type ImportPreview,
  type ImportRequest,
} from "../api/client";
import {
  CredentialEditor,
  ProviderEditor,
  RouteEditor,
  SettingField,
} from "../components/SettingsFields";
import { settingsCopy, providerReasons } from "../settingsCopy";

type Values = Record<string, unknown>;
const groups = [
  "research",
  "providers",
  "news",
  "sources",
  "cache",
  "compatibility",
] as const;

export default function Settings() {
  const { i18n: languageState } = useTranslation();
  const language = languageState.language.startsWith("zh")
    ? "zh-CN"
    : languageState.language.startsWith("ja")
      ? "ja"
      : "en";
  const text = settingsCopy[language];
  const [view, setView] = useState<ConfigurationView | null>(null);
  const [schema, setSchema] = useState<ConfigurationSchema | null>(null);
  const [capabilities, setCapabilities] = useState<Capabilities | null>(null);
  const [draft, setDraft] = useState<Values>({});
  const [credentials, setCredentials] = useState<
    Record<string, string | null | undefined>
  >({});
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [reloadVersion, setReloadVersion] = useState(0);
  const [models, setModels] = useState<string[]>([]);
  const [importInput, setImportInput] = useState<ImportRequest>({});
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem("tradingagents-sidebar-collapsed") === "true",
  );
  const fail = (cause: unknown) => {
    if (cause instanceof ApiError)
      setFieldErrors(
        Object.fromEntries(
          (cause.details ?? []).map((detail) => [
            detail.location
              .filter((part) => !["body", "values"].includes(part))[0]
              ?.split(".")[0],
            detail.message,
          ]),
        ),
      );
    setError(
      cause instanceof ApiError && cause.status === 409
        ? text.conflict
        : String(cause instanceof Error ? cause.message : cause),
    );
  };
  useEffect(() => {
    let active = true;
    void Promise.all([
      api.settings(),
      api.settingsSchema(),
      api.capabilities(),
    ]).then(
      ([data, metadata, caps]) => {
        if (!active) return;
        setView(data);
        setSchema(metadata);
        setCapabilities(caps);
        setDraft((old) =>
          reloadVersion && Object.keys(old).length
            ? old
            : (structuredClone(data.values) as Values),
        );
        setError("");
      },
      (cause) => {
        if (active) setError(String(cause));
      },
    );
    return () => {
      active = false;
    };
  }, [reloadVersion]);
  const update = (key: string, value: unknown) => {
    setDraft((old) => ({ ...old, [key]: value }));
    setNotice("");
  };
  const fieldMatches = (field: ConfigurationField) =>
    [field.key, ...Object.values(field.label), ...(field.env_names ?? [])]
      .join(" ")
      .toLowerCase()
      .includes(query.toLowerCase());
  const providerMatches = (provider: string) =>
    !query ||
    `${provider} ${schema?.providers[provider]} ${Object.entries(
      schema?.credential_owners ?? {},
    )
      .filter(([, owner]) => owner === provider)
      .map(([name]) => name)
      .join(" ")}`
      .toLowerCase()
      .includes(query.toLowerCase()) ||
    query.toLowerCase().includes("providers");
  async function operation(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setFieldErrors({});
    setNotice("");
    try {
      await action();
    } catch (cause) {
      fail(cause);
    } finally {
      setBusy(false);
    }
  }
  function keysFor(group: string) {
    return Object.entries(schema?.credential_owners ?? {})
      .filter(([, owner]) =>
        group === "providers"
          ? owner in (schema?.providers ?? {})
          : group === "sources" && !(owner in (schema?.providers ?? {})),
      )
      .map(([name]) => name);
  }
  async function save(group: string, reset = false) {
    if (!view || !schema) return;
    const fields = schema.fields.filter((field) => field.group === group);
    await operation(async () => {
      const values = Object.fromEntries(
        fields.map((field) => [field.key, draft[field.key]]),
      );
      const changes = Object.fromEntries(
        keysFor(group)
          .filter((key) => credentials[key] !== undefined)
          .map((key) => [key, credentials[key]!]),
      );
      const result = await api.saveSettings({
        revision: view.revision,
        ...(reset
          ? { reset_fields: fields.map((field) => field.key) }
          : { values: values as ConfigurationValues, credentials: changes }),
      });
      setView(result);
      setDraft((old) => ({
        ...old,
        ...Object.fromEntries(
          fields.map((field) => [
            field.key,
            (result.values as Values)[field.key],
          ]),
        ),
      }));
      setCredentials((old) =>
        Object.fromEntries(
          Object.entries(old).filter(([key]) => !keysFor(group).includes(key)),
        ),
      );
      setNotice(text.saved);
      void api
        .capabilities()
        .then(setCapabilities)
        .catch(() => {});
    });
  }
  function cancel(group: string) {
    if (!view || !schema) return;
    setDraft((old) => ({
      ...old,
      ...Object.fromEntries(
        schema.fields
          .filter((field) => field.group === group)
          .map((field) => [field.key, (view.values as Values)[field.key]]),
      ),
    }));
    setCredentials((old) =>
      Object.fromEntries(
        Object.entries(old).filter(([key]) => !keysFor(group).includes(key)),
      ),
    );
  }
  function credentialEditor(name: string) {
    return (
      <CredentialEditor
        key={`${name}-${view?.revision}`}
        name={name}
        configured={view?.credentials[name] ?? false}
        pending={credentials[name]}
        onChange={(value) =>
          setCredentials((old) => ({ ...old, [name]: value }))
        }
        text={text}
        onError={fail}
      />
    );
  }
  async function applyImport(defaults = false) {
    if (!view) return;
    await operation(async () => {
      const result = await api.applySettingsImport(
        defaults
          ? { revision: view.revision, use_defaults: true }
          : {
              ...importInput,
              revision: preview!.revision,
              fingerprint: preview!.fingerprint,
            },
      );
      setView(result);
      setDraft(structuredClone(result.values) as Values);
      setCredentials({});
      setPreview(null);
      setImportInput({});
      setNotice(text.saved);
      void api
        .capabilities()
        .then(setCapabilities)
        .catch(() => {});
    });
  }
  function renderField(field: ConfigurationField) {
    const value = draft[field.key];
    if (!schema) return null;
    if (field.kind === "providers")
      return (
        <div className="configuration-providers">
          {Object.entries(schema.providers)
            .filter(
              ([provider]) =>
                !query || fieldMatches(field) || providerMatches(provider),
            )
            .map(([provider, label]) => {
              const connections = (draft.providers ??
                {}) as ConfigurationValues["providers"];
              const connection =
                connections?.[provider] ?? schema.provider_defaults[provider];
              return (
                <article className="configuration-provider" key={provider}>
                  <h3>
                    {label}{" "}
                    <small>
                      {capabilities?.providers[provider]?.configured
                        ? text.configured
                        : text.unavailable}
                    </small>
                  </h3>
                  {capabilities?.providers[provider]?.unavailable_reason && (
                    <p className="configuration-help">
                      {providerReasons[language][
                        capabilities.providers[provider].unavailable_reason!
                      ] ?? text.unavailable}
                    </p>
                  )}
                  <ProviderEditor
                    name={provider}
                    label={label}
                    connection={connection}
                    schema={schema}
                    text={text}
                    onChange={(connection) =>
                      update("providers", {
                        ...connections,
                        [provider]: connection,
                      })
                    }
                  />
                  {Object.entries(schema.credential_owners)
                    .filter(([, owner]) => owner === provider)
                    .map(([name]) => credentialEditor(name))}
                </article>
              );
            })}
        </div>
      );
    if (field.kind === "routes")
      return (
        <div className="configuration-field">
          <h3>{field.label[language] ?? field.label.en}</h3>
          <p className="configuration-help">
            {field.description[language] ?? field.description.en}
          </p>
          {field.key === "data_vendors_by_market" ? (
            [".T", ".SS", ".SZ"].map((market) => (
              <details key={market}>
                <summary>{market}</summary>
                <RouteEditor
                  name={market}
                  value={
                    (value as Record<string, Record<string, string>>)?.[
                      market
                    ] ?? {}
                  }
                  options={schema.route_options}
                  text={text}
                  onChange={(routes) =>
                    update(field.key, {
                      ...(value as object),
                      [market]: routes,
                    })
                  }
                />
              </details>
            ))
          ) : (
            <RouteEditor
              name={field.key}
              value={value as Record<string, string>}
              options={
                field.key === "tool_vendors"
                  ? schema.tool_options
                  : schema.route_options
              }
              text={text}
              onChange={(routes) => update(field.key, routes)}
            />
          )}
        </div>
      );
    return (
      <>
        <SettingField
          field={field}
          value={value}
          onChange={(value) => update(field.key, value)}
          language={language}
          text={text}
          models={models}
        />
        <small className="configuration-source">
          {text.source}:{" "}
          {view?.sources[field.key] === "database"
            ? text.database
            : text.default}
        </small>
      </>
    );
  }
  return (
    <section className="configuration-page">
      <header className="page-header">
        <div>
          <h1>{text.title}</h1>
          <p className="subtitle">{text.subtitle}</p>
        </div>
      </header>
      <section className="interface-preferences">
        <h2>{text.interface}</h2>
        <label>
          {text.language}
          <select
            value={language}
            onChange={(event) => {
              localStorage.setItem("tradingagents-locale", event.target.value);
              void i18n.changeLanguage(event.target.value);
            }}
          >
            <option value="zh-CN">简体中文</option>
            <option value="en">English</option>
            <option value="ja">日本語</option>
          </select>
        </label>
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={collapsed}
            onChange={(event) => {
              setCollapsed(event.target.checked);
              localStorage.setItem(
                "tradingagents-sidebar-collapsed",
                String(event.target.checked),
              );
              window.dispatchEvent(new Event("tradingagents:preferences"));
            }}
          />
          {text.collapse}
        </label>
      </section>
      {error && (
        <div role="alert" className="alert">
          {error}
          <button
            className="button"
            onClick={() => setReloadVersion((value) => value + 1)}
          >
            {text.reload}
          </button>
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
      {!view && !error && <p role="status">{text.loading}</p>}
      {view && schema && (
        <>
          {!view.initialized && (
            <div className="alert">
              <p>{text.setup}</p>
              <button
                disabled={busy}
                className="button primary"
                onClick={() => void applyImport(true)}
              >
                {text.initialize}
              </button>
            </div>
          )}
          <details
            className="configuration-import"
            open={!view.initialized || undefined}
          >
            <summary>{text.import}</summary>
            <p>{text.importHint}</p>
            {(["primary", "enterprise"] as const).map((key) => (
              <label key={key}>
                {text[key]}
                <input
                  type="file"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    setPreview(null);
                    if (file)
                      void file
                        .text()
                        .then((content) =>
                          setImportInput((old) => ({ ...old, [key]: content })),
                        );
                    else setImportInput((old) => ({ ...old, [key]: null }));
                  }}
                />
              </label>
            ))}
            <label>
              {text.exclude}
              <textarea
                value={(importInput.exclude ?? []).join("\n")}
                onChange={(e) => {
                  setImportInput((old) => ({
                    ...old,
                    exclude: e.target.value.split("\n"),
                  }));
                  setPreview(null);
                }}
              />
            </label>
            <button
              disabled={busy}
              className="button"
              onClick={() =>
                void operation(async () =>
                  setPreview(
                    await api.previewSettingsImport({
                      ...importInput,
                      revision: view.revision,
                    }),
                  ),
                )
              }
            >
              {text.preview}
            </button>
            {preview && (
              <div className="configuration-preview">
                <h3>{text.changes}</h3>
                <dl>
                  {Object.entries(preview.values).map(([key, value]) => (
                    <div key={key}>
                      <dt>{key}</dt>
                      <dd>
                        <code>{JSON.stringify(value)}</code>
                      </dd>
                    </div>
                  ))}
                </dl>
                <h3>{text.credentials}</h3>
                {Object.keys(preview.credentials).map((name) => (
                  <p key={name}>
                    <code>{name}</code> · {text.configured}
                  </p>
                ))}
                {!!preview.conflicts.length && (
                  <p>
                    {text.conflicts}: {preview.conflicts.join(", ")}
                  </p>
                )}
                {!!preview.issues.length && (
                  <div role="alert">
                    <strong>{text.errors}</strong>
                    {preview.issues.map((issue, index) => (
                      <p key={index}>
                        {issue.name}: {issue.message}
                      </p>
                    ))}
                  </div>
                )}
                <button
                  className="button primary"
                  disabled={busy || !!preview.issues.length}
                  onClick={() => void applyImport()}
                >
                  {text.apply}
                </button>
              </div>
            )}
          </details>
          <label className="configuration-search">
            {text.search}
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <nav className="configuration-nav">
            {groups.map((group) => (
              <a key={group} href={`#configuration-${group}`}>
                {text[group]}
              </a>
            ))}
          </nav>
          {groups.map((group) => {
            const fields = schema.fields.filter(
              (field) =>
                field.group === group &&
                (!query ||
                  fieldMatches(field) ||
                  (field.kind === "providers" &&
                    Object.keys(schema.providers).some(providerMatches))),
            );
            const secretKeys =
              group === "sources"
                ? keysFor(group).filter(
                    (key) =>
                      !query ||
                      `${key} ${schema.credential_owners[key]}`
                        .toLowerCase()
                        .includes(query.toLowerCase()),
                  )
                : [];
            if (!fields.length && !secretKeys.length) return null;
            return (
              <section
                className="panel configuration-group"
                key={group}
                id={`configuration-${group}`}
              >
                <h2>{text[group]}</h2>
                {group === "providers" && <p>{text.keyHint}</p>}
                {fields.map((field) => (
                  <div key={field.key} data-invalid={!!fieldErrors[field.key]}>
                    {renderField(field)}
                    {["providers", "routes"].includes(field.kind) && (
                      <details className="configuration-details">
                        <summary>
                          {text.technical}: <code>{field.key}</code>
                        </summary>
                        <p>
                          {text.source}:{" "}
                          {view.sources[field.key] === "database"
                            ? text.database
                            : text.default}
                        </p>
                        <div>
                          {text.defaultValue}:{" "}
                          <code>{JSON.stringify(field.default)}</code>
                        </div>
                        {field.env_names?.map((name) => (
                          <code key={name}>{name}</code>
                        ))}
                      </details>
                    )}
                    {fieldErrors[field.key] && (
                      <p role="alert" className="configuration-field-error">
                        {fieldErrors[field.key]}
                      </p>
                    )}
                  </div>
                ))}
                {secretKeys.map(credentialEditor)}
                {group === "research" && (
                  <div>
                    <button
                      className="button"
                      disabled={busy}
                      onClick={() =>
                        void operation(async () => {
                          const catalog = await api.providerModels(
                            String(draft.llm_provider),
                            true,
                          );
                          setModels(catalog.models.map((model) => model.id));
                          if (catalog.warning)
                            setNotice(catalog.warning.message);
                        })
                      }
                    >
                      {text.models}
                    </button>
                    <small>{text.manualModel}</small>
                  </div>
                )}
                <div className="configuration-actions">
                  <button
                    disabled={busy}
                    className="button primary"
                    onClick={() => void save(group)}
                  >
                    {text.save}
                  </button>
                  <button
                    disabled={busy}
                    className="button"
                    onClick={() => cancel(group)}
                  >
                    {text.cancel}
                  </button>
                  <button
                    disabled={busy}
                    className="button"
                    onClick={() => void save(group, true)}
                  >
                    {text.reset}
                  </button>
                </div>
              </section>
            );
          })}
          <section className="panel configuration-group">
            <h2>{text.deployment}</h2>
            <p>{text.startup}</p>
            <dl>
              {Object.entries(view.deployment).map(([key, value]) => (
                <div key={key}>
                  <dt>{key}</dt>
                  <dd>{String(value)}</dd>
                </div>
              ))}
            </dl>
          </section>
        </>
      )}
    </section>
  );
}
