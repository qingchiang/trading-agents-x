import { useEffect, useState } from "react";
import { api, ApiError, type ConfigurationView, type ConfigurationSchema, type ModelConnection, type ConnectionChange } from "../api/client";
import { CredentialEditor } from "./SettingsFields";
import { connectionCopy, connectionField } from "../connectionCopy";
import type { SettingsText } from "../settingsCopy";

import { changedValues, equal, mergeDraft, refreshDraft } from "../settingsDraft";
import SettingsConflict, { DisplayValue } from "./SettingsConflict";
import SettingsSaveBar from "./SettingsSaveBar";
import { editingCopy } from "../settingsEditingCopy";

type Draft = { credentialRevision: number; baseline: ModelConnection; connection: ModelConnection; credentials: Record<string, string | null | undefined>; fresh: boolean };
const interfaceNames: Record<string, string> = { chat_completions: "Chat Completions", responses: "Responses API", anthropic: "Anthropic", google: "Google Gemini", azure: "Azure OpenAI", bedrock: "Amazon Bedrock" };
export default function ModelConnections({ view, schema, language, text, active, requestedId, focusField, onSaved, onRefresh, onError, onDirty }: {
  view: ConfigurationView; schema: ConfigurationSchema; language: string; text: SettingsText; active: boolean; requestedId?: string | null; focusField?: string;
  onRefresh: (view: ConfigurationView) => void; onSaved: (view: ConfigurationView) => void; onError: (cause: unknown) => void; onDirty: (dirty: boolean) => void;
}) {
  const c = connectionCopy(language);
  const editing = editingCopy(language);
  const [conflict, setConflict] = useState<{ id: string; server: ConfigurationView } | null>(null);
  const [restore, setRestore] = useState(false);
  const [selected, setSelected] = useState("");
  const [preset, setPreset] = useState("openai_compatible");
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (requestedId) setSelected(requestedId); }, [requestedId]);
  useEffect(() => {
    if (requestedId || !focusField || !["google_thinking_level", "anthropic_effort", "openai_reasoning_effort"].includes(focusField)) return;
    const compatible = Object.values(view.connections ?? {}).find(entry => focusField.startsWith(entry.connection.compatibility ?? "") || (focusField.startsWith("openai") && ["chat_completions", "responses", "azure"].includes(entry.connection.transport.kind ?? "")));
    if (compatible) setSelected(compatible.connection.id);
  }, [focusField, requestedId, view.connections]);
  useEffect(() => { setRestore(false); }, [selected, active]);
  useEffect(() => {
    setDrafts(old => Object.fromEntries(Object.entries(old).map(([id, draft]) => {
      const current = view.connections?.[id]?.connection;
      if (!current || draft.fresh) return [id, draft];
      const next = refreshDraft(draft.baseline, draft.connection, current);
      return [id, { ...draft, baseline: next.base, connection: next.value }];
    })));
  }, [view.connections]);
  const draft = drafts[selected];
  const saved = view.connections?.[selected];
  const conn = draft?.connection ?? saved?.connection;
  const isDirty = (d: Draft | undefined) => !!d && (d.fresh || !equal(d.baseline, d.connection) || Object.values(d.credentials).some(v => v !== undefined));
  useEffect(() => onDirty(Object.values(drafts).some(isDirty)), [drafts, onDirty]);
  function update(next: Partial<ModelConnection>, secrets?: Draft["credentials"]) {
    if (!conn) return;
    setDrafts(old => ({ ...old, [selected]: { credentialRevision: old[selected]?.credentialRevision ?? conn.revision ?? view.revision, baseline: old[selected]?.baseline ?? conn, connection: { ...conn, ...next },
      credentials: secrets ?? old[selected]?.credentials ?? {}, fresh: old[selected]?.fresh ?? false } }));
  }
  function discard() { if (conflict?.id === selected) onRefresh(conflict.server); setConflict(null); setRestore(false); setDrafts(old => { const next = { ...old }; delete next[selected]; return next; }); }
  async function save(action: ConnectionChange["action"] = draft?.fresh ? "create" : "update") {
    if (!conn) return;
    if (action === "delete" && !window.confirm(c.confirmDelete)) return;
    if (!draft?.fresh && !saved) { onError(new Error(editing.deleted)); return; }
    const merged = mergeDraft(draft?.baseline ?? conn, conn, saved?.connection ?? conn);
    if (action === "update" && (merged.conflicts.length || (Object.values(draft?.credentials ?? {}).some(value => value !== undefined) && draft?.credentialRevision !== (saved?.connection.revision ?? view.revision)))) { setConflict({ id: selected, server: view }); return; }
    setBusy(true);
    try {
      const change: ConnectionChange = { action, id: conn.id };
      if (action === "create" || action === "update") {
        const values = action === "create" ? merged.value : changedValues(saved!.connection, merged.value);
        for (const key of ["name", "preset", "transport", "compatibility", "enabled", "discovery", "key_required", "reasoning_defaults"] as const) {
          if (key in values) Object.assign(change, { [key]: values[key] });
        }
        change.credentials = Object.fromEntries(Object.entries(draft?.credentials ?? {}).filter((entry): entry is [string, string | null] => entry[1] !== undefined));
      }
      const result = await api.saveSettings({ revision: view.revision, connection_changes: [change] });
      discard(); onSaved(result);
      if (action === "delete") setSelected("");
    } catch (cause) {
      if (cause instanceof ApiError && cause.status === 409) {
        try {
          const latest = await api.settings();
          if (action === "update") setConflict({ id: selected, server: latest });
          else { onRefresh(latest); onError(cause); }
        } catch (error) { onError(error); }
      } else onError(cause);
    } finally { setBusy(false); }
  }

  function create() {
    const source = schema.presets?.[preset];
    if (!source) return;
    const id = crypto.randomUUID();
    setDrafts(old => ({ ...old, [id]: { credentialRevision: view.revision, baseline: structuredClone(source), connection: { ...structuredClone(source), id, name: source.name }, credentials: {}, fresh: true } }));
    setSelected(id);
  }
  const entries = { ...Object.fromEntries(Object.entries(view.connections ?? {}).map(([id, v]) => [id, v.connection])),
    ...Object.fromEntries(Object.entries(drafts).map(([id, v]) => [id, v.connection])) };
  if (!active) return null;
  const conflictServer = conflict?.id === selected ? conflict.server.connections?.[selected]?.connection : undefined;
  const deleted = !draft?.fresh && !!conn && (!saved || (conflict?.id === selected && !conflictServer));
  const templateChanges = conn ? changedValues(conn, { ...conn, ...Object.fromEntries(Object.entries(conn.template ?? {}).filter(([key]) => ["transport", "compatibility", "discovery", "key_required", "reasoning_defaults"].includes(key))) }) : {};
  const transport = conn?.transport as Record<string, unknown> | undefined;
  const discoveryOptions = Array.from(new Set([conn?.discovery, "custom", ...Object.values(schema.presets ?? {}).filter(p => p.transport.kind === conn?.transport.kind || (["chat_completions", "responses"].includes(p.transport.kind ?? "") && ["chat_completions", "responses"].includes(conn?.transport.kind ?? ""))).map(p => p.discovery)])).filter(Boolean);
  const setTransport = (key: string, value: unknown) => update({ transport: { ...conn!.transport, [key]: value } as ModelConnection["transport"] });
  return <section className="connections-workspace">
    <div className="connection-toolbar"><h2>{c.connections}</h2>{(!selected || !conn) && <><label>{c.preset}<select value={preset} onChange={e => setPreset(e.target.value)}>
      {Object.entries(schema.presets ?? {}).map(([id, p]) => <option key={id} value={id}>{p.name}</option>)}
    </select></label><button className="button primary" onClick={create}>{c.add}</button></>}</div>
    {!selected || !conn ? <div className="connection-cards">
      {!Object.keys(entries).length && <p>{c.empty}</p>}
      {Object.entries(entries).map(([id, item]) => <article className="panel connection-card" key={id}>
        <h3>{item.name}</h3><p>{interfaceNames[item.transport.kind ?? ""]}</p><p className="connection-address">{"base_url" in item.transport ? item.transport.base_url : ("region" in item.transport ? item.transport.region : "")}</p>
        <small>{item.enabled === false ? c.disabled : view.connections?.[id]?.selectable ? c.ready : text.unavailable}</small>
        {isDirty(drafts[id]) && <p>{c.dirty}</p>}<button className="button" onClick={() => setSelected(id)}>{c.edit}</button>
      </article>)}
    </div> : <div className="panel connection-editor">
      <header className="connection-editor-heading"><div><h3>{conn.name}</h3><p className="connection-address">{interfaceNames[conn.transport.kind ?? ""]}</p></div><button className="button" onClick={() => setSelected("")}>{c.back}</button></header>
      {!!saved?.missing_fields.length && <p className="alert">{c.missing}: {saved.missing_fields.map(field => connectionField(language, field)).join(", ")}</p>}
      <fieldset className="settings-edit-fields" disabled={conflict?.id === selected}>
      <div className="configuration-grid">
        <label>{c.name}<input id="connection-name" value={conn.name} onChange={e => update({ name: e.target.value })} /></label>
        <label className="checkbox-label"><input id="connection-enabled" type="checkbox" checked={conn.enabled !== false} onChange={e => update({ enabled: e.target.checked })} />{c.enabled}</label>
        {Object.entries(transport ?? {}).filter(([key]) => key !== "kind").map(([key, value]) => <label key={key}>{connectionField(language, key)}
          {key === "auth_mode" ? <select id={`connection-${key}`} value={String(value)} onChange={e => setTransport(key, e.target.value)}>{["system", "static", "bearer"].map(mode => <option key={mode} value={mode}>{text[mode as "system" | "static" | "bearer"]}</option>)}</select>
            : <input id={`connection-${key}`} value={String(value ?? "")} onChange={e => setTransport(key, e.target.value || null)} />}</label>)}
      </div>
      <section className="connection-credentials"><h3>{c.credentials}</h3><p className="configuration-help">{text.keyHint}</p><div className="configuration-grid">
        {(conn.transport.kind === "bedrock" ? ["access_key_id", "secret_access_key", "session_token", "bearer_token"] : ["api_key"]).map(field => <CredentialEditor
          key={`${selected}-${field}-${view.revision}`} connectionId={selected} name={field} configured={saved?.credentials[field] ?? false}
          pending={draft?.credentials[field]} onChange={value => update({}, { ...draft?.credentials, [field]: value })} text={text} onError={onError} />)}
      </div></section>
      <details open={!!focusField || undefined}><summary>{c.advanced}</summary><p className="configuration-help">{schema.group_descriptions?.compatibility?.[language]}</p><div className="configuration-grid">
        <label>{connectionField(language, "discovery")}<select id="connection-discovery" value={conn.discovery} onChange={e => update({ discovery: e.target.value as ModelConnection["discovery"] })}>{discoveryOptions.map(value => <option key={value} value={value}>{schema.presets?.[value!]?.name ?? value}</option>)}</select></label>
        {(["chat_completions", "responses"].includes(conn.transport.kind ?? "")) && <>
          <label>{c.protocol}<select id="connection-kind" value={conn.transport.kind} onChange={e => setTransport("kind", e.target.value)}><option value="chat_completions">Chat Completions</option><option value="responses">Responses</option></select></label>
          <label>{c.compatibility}<select id="connection-compatibility" value={conn.compatibility} onChange={e => update({ compatibility: e.target.value })}>{Object.entries(schema.presets ?? {}).filter(([, p]) => ["chat_completions", "responses"].includes(p.transport.kind ?? "")).map(([id, p]) => <option value={id} key={id}>{p.name}</option>)}</select></label>
          <label className="checkbox-label"><input id="connection-key_required" type="checkbox" checked={conn.key_required ?? true} onChange={e => update({ key_required: e.target.checked })} />{c.required}</label>
        </>}
        {schema.fields.filter(f => f.group === "compatibility" && (f.key.startsWith(conn.transport.kind ?? "") || (f.key.startsWith("openai") && ["chat_completions", "responses", "azure"].includes(conn.transport.kind ?? "")))).map(f => <label key={f.key}>{f.label[language] ?? f.label.en}<select id={`setting-${f.key}`} value={conn.reasoning_defaults?.[f.key] ?? ""} onChange={e => update({ reasoning_defaults: { ...conn.reasoning_defaults, [f.key]: e.target.value || null } })}><option value="">{text.unset}</option>{f.options?.map(option => <option key={option} value={option}>{f.option_labels?.[option]?.[language] ?? option}</option>)}</select></label>)}
      </div></details>
      </fieldset>
      {deleted && <p className="alert" role="alert">{editing.deleted}</p>}
      {conflict?.id === selected && conflictServer && draft && <SettingsConflict key={`${selected}-${conflict.server.revision}`} language={language}
        conflicts={mergeDraft(draft.baseline, conn, conflictServer).conflicts}
        credentials={Object.fromEntries(Object.entries(draft.credentials).filter(([, v]) => v !== undefined).map(([key, value]) => [key, value === null ? "remove" : "replace"]))}
        label={path => connectionField(language, path.at(-1) ?? "")}
        onApply={(choices, secrets) => {
          const value = mergeDraft(draft.baseline, conn, conflictServer, choices).value;
          setDrafts(old => ({ ...old, [selected]: { ...draft, credentialRevision: conflictServer.revision ?? conflict.server.revision, baseline: conflictServer, connection: value, credentials: Object.fromEntries(Object.entries(draft.credentials).filter(([key]) => secrets[key] !== "server")) } }));
          onRefresh(conflict.server); setConflict(null);
        }} />}
      {restore && <section className="settings-conflict" aria-label={editing.resetPreview}><h3>{editing.resetPreview}</h3><p>{editing.resetHint}</p>
        {Object.entries(templateChanges).map(([key, value]) => <div key={key}><strong>{connectionField(language, key)}</strong><div className="conflict-values"><DisplayValue value={conn[key as keyof ModelConnection]} /><DisplayValue value={value} /></div></div>)}
        {!Object.keys(templateChanges).length && <p>{editing.noChanges}</p>}
        <div className="configuration-actions"><button className="button" onClick={() => { update(templateChanges); setRestore(false); }}>{editing.confirm}</button><button className="button" onClick={() => setRestore(false)}>{text.cancel}</button></div>
      </section>}
      <SettingsSaveBar label={`${conn.name} · ${isDirty(draft) ? c.dirty : editing.noChanges}`}>
        <button disabled={busy || !isDirty(draft) || deleted || conflict?.id === selected} className="button primary" onClick={() => void save()}>{text.save}</button>
        <button className="button" disabled={busy || !draft} onClick={discard}>{text.cancel}</button>
        {!draft?.fresh && <><button className="button" disabled={busy || deleted || conflict?.id === selected} onClick={() => setRestore(true)}>{conn.template_origin === "upgrade" ? editing.upgrade : editing.creation}</button><button className="button danger" disabled={busy || deleted} onClick={() => void save("delete")}>{c.delete}</button></>}
      </SettingsSaveBar>
    </div>}
  </section>;
}
