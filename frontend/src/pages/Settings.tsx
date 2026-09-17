import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import ModelConnections from "../components/ModelConnections";
import RoleConnections from "../components/RoleConnections";
import { connectionCopy } from "../connectionCopy";
import { Link, useLocation } from "../router";
import i18n from "../i18n";
import {
  api,
  ApiError,
  type ConfigurationField,
  type ConfigurationSchema,
  type ConfigurationValues,
  type ConfigurationView,
  type ImportPreview,
  type ImportRequest,
} from "../api/client";
import {
  CredentialEditor,
  RouteEditor,
  SettingField,
} from "../components/SettingsFields";
import { settingsCopy } from "../settingsCopy";

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
  const c = connectionCopy(language);
  const location = useLocation();
  const category = location.pathname.split("/")[2] || "connections";
  const [dataTab, setDataTab] = useState("sources");
  const [connectionsDirty, setConnectionsDirty] = useState(false);
  const [latest, setLatest] = useState<ConfigurationView | null>(null);
  const [view, setView] = useState<ConfigurationView | null>(null);
  const [schema, setSchema] = useState<ConfigurationSchema | null>(null);
  const [draft, setDraft] = useState<Values>({});
  const [credentials, setCredentials] = useState<
    Record<string, string | null | undefined>
  >({});
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
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
    ]).then(
      ([data, metadata]) => {
        if (!active) return;
        setView(data);
        setSchema(metadata);
        setDraft(structuredClone(data.values) as Values);
        setError("");
      },
      (cause) => {
        if (active) setError(String(cause));
      },
    );
    return () => {
      active = false;
    };
  }, []);
  const dirty = connectionsDirty || (view !== null && Object.keys(draft).some(key => JSON.stringify(draft[key]) !== JSON.stringify((view.values as Values)[key]))) || Object.keys(credentials).length > 0;
  useEffect(() => {
    if (!dirty) return;
    const unload = (event: BeforeUnloadEvent) => { event.preventDefault(); };
    const leaving = (event: Event) => {
      const detail = (event as CustomEvent<{ path: string }>).detail;
      if (!new URL(detail.path, window.location.origin).pathname.startsWith("/settings") && !window.confirm(c.leave)) event.preventDefault();
    };
    window.addEventListener("beforeunload", unload);
    window.addEventListener("tradingagents:before-navigate", leaving);
    return () => { window.removeEventListener("beforeunload", unload); window.removeEventListener("tradingagents:before-navigate", leaving); };
  }, [dirty, c.leave]);
  useEffect(() => {
    if (!location.hash) return;
    const frame = requestAnimationFrame(() => {
      const element = document.getElementById(location.hash.slice(1)) ?? (location.hash === "#setting-quick_connection_id" ? document.getElementById("setting-deep_connection_id") : null);
      if (!element) return;
      if (element instanceof HTMLDetailsElement) element.open = true;
      for (let parent = element.parentElement; parent; parent = parent.parentElement) {
        if (parent instanceof HTMLDetailsElement) parent.open = true;
      }
      element.scrollIntoView?.({ block: "center" }); element.focus();
    });
    return () => cancelAnimationFrame(frame);
  }, [location.hash, location.pathname, view]);
  const update = (key: string, value: unknown) => {
    setDraft((old) => ({ ...old, [key]: value }));
    setNotice("");
  };
  const fieldMatches = (field: ConfigurationField) =>
    [field.key, ...Object.values(field.label), ...(field.env_names ?? [])]
      .join(" ")
      .toLowerCase()
      .includes(query.toLowerCase());
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
        fields.filter(field => field.key !== "llm_provider").map((field) => [field.key, draft[field.key]]),
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
    });
  }
  function renderField(field: ConfigurationField) {
    const value = draft[field.key];
    if (!schema) return null;
    if (field.kind === "routes")
      return (
        <details id={`setting-${field.key}`} className="configuration-field route-settings" tabIndex={-1}>
          <summary>{field.label[language] ?? field.label.en}</summary>
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
        </details>
      );
    return (
      <>
        <SettingField
          field={field}
          value={value}
          onChange={(value) => update(field.key, value)}
          language={language}
          text={text}
          models={[]}
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
      <section className="interface-preferences" hidden={category !== "interface"}>
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
          {Object.entries(fieldErrors).filter(([key]) => key === "connection_changes" || key === "connections" || key.endsWith("connection_id") || key.endsWith("reasoning_effort")).map(([key, message]) => <p key={key}><code>{key}</code>: {message}</p>)}
          <button
            className="button"
            onClick={() => void api.settings().then(setLatest).catch(fail)}
          >
            {c.latest}
          </button>
        </div>
      )}
      {latest && <div className="panel"><h3>{c.server}</h3><pre>{JSON.stringify({ values: latest.values, connections: latest.connections }, null, 2)}</pre><h3>{c.local}</h3><pre>{JSON.stringify(draft, null, 2)}</pre><button className="button" onClick={() => { setView(latest); setLatest(null); setError(""); }}>{c.reapply}</button></div>}
      {notice && <p role="status">{notice}</p>}
      {dirty && <p role="status">{c.dirty}</p>}
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
            hidden={category !== "storage" && view.initialized}
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
                <h3>{c.connections}</h3>{Object.entries(preview.connection_targets ?? {}).map(([name, target]) => <p key={name}><code>{name}</code> → <code>{target}</code></p>)}
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
          <nav className="configuration-nav" aria-label={text.title}>
            {(["connections", "research", "data", "storage", "interface"] as const).map(key => <Link key={key} to={`/settings/${key}`} aria-current={category === key ? "page" : undefined}>{c[key]}</Link>)}
          </nav>
          {query && <div className="panel configuration-search-results"><h2>{c.results}</h2>{schema.fields.filter(fieldMatches).map(field => {
            const target = ["llm_provider", "providers"].includes(field.key) || field.group === "providers" || field.group === "compatibility" ? "connections" : field.group === "news" || field.group === "sources" ? "data" : field.group === "cache" ? "storage" : "research";
            return <Link key={field.key} to={`/settings/${target}#setting-${field.key}`} onClick={() => { setDataTab(field.group); setQuery("");  }}>{field.label[language] ?? field.label.en} <code>{field.key}</code></Link>;
          })}{Object.entries(schema.credential_owners).filter(([name, owner]) => `${name} ${owner}`.toLowerCase().includes(query.toLowerCase())).map(([name, owner]) => {
            const match = Object.values(view.connections ?? {}).find(entry => entry.connection.preset === owner);
            return <Link key={name} to={match ? `/settings/connections?connection=${match.connection.id}` : "/settings/data"} onClick={() => { setQuery(""); setDataTab("sources"); }}>{name}</Link>;
          })}{Object.values(view.connections ?? {}).filter(entry => `${entry.connection.name} ${entry.connection.id}`.toLowerCase().includes(query.toLowerCase())).map(entry => <Link key={entry.connection.id} to={`/settings/connections?connection=${entry.connection.id}`} onClick={() => setQuery("")}>{entry.connection.name}</Link>)}</div>}
          <ModelConnections view={view} schema={schema} text={text} language={language} active={category === "connections"} requestedId={new URLSearchParams(location.search).get("connection")} focusField={location.hash.replace("#setting-", "")} onSaved={saved => { setView(saved); setError(""); setNotice(text.saved); }} onError={fail} onDirty={setConnectionsDirty} />
          {category === "data" && <nav className="configuration-subnav">{["sources", "news"].map(key => <button className="button" key={key} aria-pressed={dataTab === key} onClick={() => setDataTab(key)}>{key === "sources" ? c.sources : c.news}</button>)}</nav>}
          {groups.filter(group => category === "research" ? group === "research" : category === "data" ? group === dataTab : category === "storage" ? group === "cache" : false).map((group) => {
            const fields = schema.fields.filter(
              (field) =>
                !["llm_provider", "quick_connection_id", "deep_connection_id", "quick_think_llm", "deep_think_llm", "quick_reasoning_effort", "deep_reasoning_effort"].includes(field.key) && field.group === group,
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
                {group === "research" && <RoleConnections language={language} connections={view.connections ?? {}} value={{ quick: { connection: String(draft.quick_connection_id ?? ""), model: String(draft.quick_think_llm ?? ""), reasoning: String(draft.quick_reasoning_effort ?? "") }, deep: { connection: String(draft.deep_connection_id ?? ""), model: String(draft.deep_think_llm ?? ""), reasoning: String(draft.deep_reasoning_effort ?? "") } }} onChange={roles => setDraft(old => ({ ...old, quick_connection_id: roles.quick.connection, deep_connection_id: roles.deep.connection, quick_think_llm: roles.quick.model, deep_think_llm: roles.deep.model, quick_reasoning_effort: roles.quick.reasoning || null, deep_reasoning_effort: roles.deep.reasoning || null }))} />}
                <div className="configuration-grid">{fields.map((field) => (
                  <div key={field.key} className={field.kind === "routes" ? "route-section" : undefined} data-invalid={!!fieldErrors[field.key]}>
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
                {secretKeys.map(credentialEditor)}</div>
                <div className="configuration-actions sticky-save">
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
          <section className="panel configuration-group" hidden={category !== "storage"}>
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
