import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import SettingsSearch from "./SettingsSearch";
import ModelConnections from "./ModelConnections";
import RoleConnections from "./RoleConnections";
import { connectionCopy } from "./connectionCopy";
import { Link, useLocation } from "../../app/router";
import {
  api,
  ApiError,
  type ConfigurationField,
  type ConfigurationSchema,
  type ConfigurationValues,
  type ConfigurationView,
  type ImportPreview,
  type ImportRequest,
} from "../../shared/api/client";
import {
  CredentialEditor,
  RouteEditor,
  SettingField,
} from "./SettingsFields";
import { settingsCopy } from "./settingsCopy";

import { changedValues, equal, mergeDraft, refreshDraft } from "./settingsDraft";
import SettingsConflict from "./SettingsConflict";
import SettingsSaveBar from "./SettingsSaveBar";
import { editingCopy } from "./settingsEditingCopy";

type Values = Record<string, unknown>;
const groups = [
  "research",
  "news",
  "sources",
  "cache",
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
  const editing = editingCopy(language);
  const navRef = useRef<HTMLElement>(null);
  const location = useLocation();
  const requestedCategory = location.pathname.split("/")[2];
  const category = ["connections", "research", "data", "storage"].includes(requestedCategory)
    ? requestedCategory : "connections";
  const [dataTab, setDataTab] = useState("sources");
  const [connectionsDirty, setConnectionsDirty] = useState(false);
  const [baseline, setBaseline] = useState<Values>({});
  const [conflict, setConflict] = useState<{ group: string; server: ConfigurationView; base: Values; local: Values } | null>(null);
  const [view, setView] = useState<ConfigurationView | null>(null);
  const [schema, setSchema] = useState<ConfigurationSchema | null>(null);
  const [draft, setDraft] = useState<Values>({});
  const [credentials, setCredentials] = useState<
    Record<string, string | null | undefined>
  >({});
  const [credentialRevisions, setCredentialRevisions] = useState<Record<string, number>>({});
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [importInput, setImportInput] = useState<ImportRequest>({});
  const [preview, setPreview] = useState<ImportPreview | null>(null);
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
        setBaseline(structuredClone(data.values) as Values);
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
  const dirty = connectionsDirty || (view !== null && Object.keys(draft).some(key => !equal(draft[key], baseline[key]))) || Object.values(credentials).some(value => value !== undefined);
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
    let frame = 0;
    let attempts = 0;
    const locate = () => {
      const element = document.getElementById(location.hash.slice(1)) ?? (location.hash === "#setting-quick_connection_id" ? document.getElementById("setting-deep_connection_id") : null);
      if (!element) { if (++attempts < 30) frame = requestAnimationFrame(locate); return; }
      if (element instanceof HTMLDetailsElement) element.open = true;
      for (let parent = element.parentElement; parent; parent = parent.parentElement) {
        if (parent instanceof HTMLDetailsElement) parent.open = true;
      }
      element.scrollIntoView?.({ block: "center" }); element.focus({ preventScroll: true });
    };
    frame = requestAnimationFrame(locate);
    return () => cancelAnimationFrame(frame);
  }, [location.hash, location.pathname, location.search, dataTab, view, schema]);
  useEffect(() => {
    const nav = navRef.current;
    const item = nav?.querySelector<HTMLElement>('[aria-current="page"]');
    if (nav && item) nav.scrollLeft = item.offsetLeft - nav.offsetLeft - (nav.clientWidth - item.offsetWidth) / 2;
  }, [category, language, view]);
  useEffect(() => {
    const key = location.hash.replace("#setting-", "");
    const group = schema?.fields.find(field => field.key === key)?.group;
    if (category === "data" && (group === "sources" || group === "news")) setDataTab(group);
    if (category === "data" && location.hash.startsWith("#credential-")) setDataTab("sources");
  }, [category, location.hash, schema]);
  const update = (key: string, value: unknown) => {
    setDraft((old) => ({ ...old, [key]: value }));
    setNotice("");
  };
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
  function groupValues(values: Values, group: string) {
    return Object.fromEntries((schema?.fields ?? []).filter(field => field.group === group && field.key !== "llm_provider").map(field => [field.key, values[field.key]]));
  }
  function receive(result: ConfigurationView, savedGroup?: string) {
    const next = refreshDraft(baseline, draft, result.values as Values);
    if (savedGroup) {
      Object.assign(next.base, groupValues(result.values as Values, savedGroup));
      Object.assign(next.value, groupValues(result.values as Values, savedGroup));
    }
    setBaseline(next.base); setDraft(next.value); setView(result);
  }
  function groupDirty(group: string) {
    return !equal(groupValues(baseline, group), groupValues(draft, group)) || keysFor(group).some(key => credentials[key] !== undefined);
  }
  async function save(group: string, reset = false) {
    if (!view || !schema) return;
    const base = groupValues(baseline, group);
    const local = reset ? Object.fromEntries(schema.fields.filter(f => f.group === group && f.key !== "llm_provider").map(f => [f.key, f.default])) : groupValues(draft, group);
    const server = groupValues(view.values as Values, group);
    const merged = mergeDraft(base, local, server);
    if (merged.conflicts.length || (!reset && keysFor(group).some(key => credentials[key] !== undefined && credentialRevisions[key] !== view.revision))) { setConflict({ group, server: view, base, local }); return; }
    await operation(async () => {
      try {
        const changes = Object.fromEntries(keysFor(group).filter(key => credentials[key] !== undefined).map(key => [key, credentials[key]!]));
        const result = await api.saveSettings({ revision: view.revision,
          ...(reset ? { reset_fields: Object.keys(local) } : { values: changedValues(server, merged.value) as ConfigurationValues, credentials: changes }),
        });
        receive(result, group);
        if (!reset) setCredentials(old => Object.fromEntries(Object.entries(old).filter(([key]) => !keysFor(group).includes(key))));
        setConflict(null); setNotice(text.saved);
      } catch (cause) {
        if (cause instanceof ApiError && cause.status === 409) {
          const latest = await api.settings();
          setConflict({ group, server: latest, base, local });
        } else throw cause;
      }
    });
  }
  function cancel(group: string) {
    if (!view) return;
    receive(conflict?.group === group ? conflict.server : view, group);
    setCredentials(old => Object.fromEntries(Object.entries(old).filter(([key]) => !keysFor(group).includes(key))));
    if (conflict?.group === group) setConflict(null);
  }
  function credentialEditor(name: string) {
    return (
      <CredentialEditor
        key={`${name}-${view?.revision}`}
        name={name}
        label={schema?.credential_metadata?.[name]?.label}
        description={schema?.credential_metadata?.[name]?.description[language]}
        configured={view?.credentials[name] ?? false}
        pending={credentials[name]}
        onChange={(value) => {
          if (credentials[name] === undefined && view) setCredentialRevisions(old => ({ ...old, [name]: view.revision }));
          setCredentials(old => ({ ...old, [name]: value }));
        }}
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
      setBaseline(structuredClone(result.values) as Values);
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
          source={view?.sources[field.key] === "database" ? text.database : text.default}
        />

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
      {error && (
        <div role="alert" className="alert">
          {error}
          {Object.entries(fieldErrors).filter(([key]) => key === "connection_changes" || key === "connections" || key.endsWith("connection_id") || key.endsWith("reasoning_effort")).map(([key, message]) => <p key={key}><code>{key}</code>: {message}</p>)}

        </div>
      )}

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
          <label className="configuration-search">
            {text.search}
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </label>
          <nav ref={navRef} className="configuration-nav" aria-label={text.title}>
            {(["connections", "research", "data", "storage"] as const).map(key => <Link key={key} to={`/settings/${key}`} aria-label={c[key]} aria-current={category === key ? "page" : undefined}>{c[key]}{(key === "connections" ? connectionsDirty : key === "research" ? groupDirty("research") : key === "data" ? groupDirty("sources") || groupDirty("news") : groupDirty("cache")) && <span className="draft-dot" aria-label={c.dirty}> •</span>}</Link>)}
          </nav>
          <SettingsSearch query={query} schema={schema} view={view} language={language} onNavigate={group => { if (["sources", "news"].includes(group)) setDataTab(group); setQuery(""); }} />
          <ModelConnections view={view} schema={schema} text={text} language={language} active={category === "connections"} requestedId={new URLSearchParams(location.search).get("connection")} focusField={location.hash.replace("#setting-", "")} onRefresh={receive} onSaved={saved => { receive(saved); setError(""); setNotice(text.saved); }} onError={fail} onDirty={setConnectionsDirty} />
          {category === "data" && <nav className="configuration-subnav">{["sources", "news"].map(key => <button className="button" key={key} aria-pressed={dataTab === key} onClick={() => setDataTab(key)}>{key === "sources" ? c.sources : c.news}</button>)}</nav>}
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
          {groups.filter(group => category === "research" ? group === "research" : category === "data" ? group === dataTab : category === "storage" ? group === "cache" : false).map((group) => {
            const fields = schema.fields.filter(
              (field) =>
                field.key !== "models" && field.group === group,
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
                <p className="configuration-help">{schema.group_descriptions?.[group]?.[language] ?? (group === "research" ? editing.future : "")}</p>
                <fieldset className="settings-edit-fields" disabled={conflict?.group === group}>
                {group === "research" && <div id="setting-models"><RoleConnections language={language} connections={view.connections ?? {}} value={{ quick: { connection: String((draft as ConfigurationValues).models?.quick?.connection_id ?? ""), model: String((draft as ConfigurationValues).models?.quick?.model ?? ""), reasoning: String((draft as ConfigurationValues).models?.quick?.reasoning_effort ?? "") }, deep: { connection: String((draft as ConfigurationValues).models?.deep?.connection_id ?? ""), model: String((draft as ConfigurationValues).models?.deep?.model ?? ""), reasoning: String((draft as ConfigurationValues).models?.deep?.reasoning_effort ?? "") } }} onChange={roles => setDraft(old => ({ ...old, models: {quick: {connection_id: roles.quick.connection, model: roles.quick.model, reasoning_effort: roles.quick.reasoning || null}, deep: {connection_id: roles.deep.connection, model: roles.deep.model, reasoning_effort: roles.deep.reasoning || null}} }))} /></div>}
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
                </fieldset>
                {conflict?.group === group && <SettingsConflict key={`${group}-${conflict.server.revision}`} language={language}
                  conflicts={mergeDraft(conflict.base, conflict.local, groupValues(conflict.server.values as Values, group)).conflicts}
                  credentials={Object.fromEntries(keysFor(group).filter(key => credentials[key] !== undefined).map(key => [key, credentials[key] === null ? "remove" : "replace"]))}
                  label={path => schema.fields.find(f => f.key === path[0])?.label[language] ?? path.join(" · ")}
                  onApply={(choices, secretChoices) => {
                    const merged = mergeDraft(conflict.base, conflict.local, groupValues(conflict.server.values as Values, group), choices);
                    const next = refreshDraft(baseline, draft, conflict.server.values as Values);
                    setBaseline({ ...next.base, ...groupValues(conflict.server.values as Values, group) });
                    setDraft({ ...next.value, ...merged.value }); setView(conflict.server);
                    setCredentials(old => Object.fromEntries(Object.entries(old).filter(([key]) => secretChoices[key] !== "server")));
                    setCredentialRevisions(old => ({ ...old, ...Object.fromEntries(keysFor(group).map(key => [key, conflict.server.revision])) }));
                    setConflict(null); setError("");
                  }} />}
                <SettingsSaveBar label={`${text[group]} · ${groupDirty(group) ? c.dirty : editing.noChanges}`}>
                  <button disabled={busy || !groupDirty(group) || conflict?.group === group} className="button primary" onClick={() => void save(group)}>{text.save}</button>
                  <button disabled={busy || !groupDirty(group)} className="button" onClick={() => cancel(group)}>{text.cancel}</button>
                  <button disabled={busy || conflict?.group === group} className="button" onClick={() => void save(group, true)}>{text.reset}</button>
                </SettingsSaveBar>
              </section>
            );
          })}
          <details className="panel configuration-group configuration-deployment" hidden={category !== "storage"}>
            <summary>{text.deployment}</summary>
            <p>{text.startup}</p>
            <dl>
              {Object.entries(view.deployment).map(([key, value]) => (
                <div key={key}>
                  <dt>{key}</dt>
                  <dd>{String(value)}</dd>
                </div>
              ))}
            </dl>
          </details>
        </>
      )}
    </section>
  );
}
