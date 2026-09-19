import { useEffect, useState } from "react";
import { api, type ConfigurationView, type ConfigurationSchema, type ModelConnection, type ConnectionChange } from "../api/client";
import { CredentialEditor } from "./SettingsFields";
import { connectionCopy, connectionField } from "../connectionCopy";
import type { SettingsText } from "../settingsCopy";

type Draft = { connection: ModelConnection; credentials: Record<string, string | null | undefined>; fresh: boolean };
const interfaceNames: Record<string, string> = { chat_completions: "Chat Completions", responses: "Responses API", anthropic: "Anthropic", google: "Google Gemini", azure: "Azure OpenAI", bedrock: "Amazon Bedrock" };
export default function ModelConnections({ view, schema, language, text, active, requestedId, focusField, onSaved, onError, onDirty }: {
  view: ConfigurationView; schema: ConfigurationSchema; language: string; text: SettingsText; active: boolean; requestedId?: string | null; focusField?: string;
  onSaved: (view: ConfigurationView) => void; onError: (cause: unknown) => void; onDirty: (dirty: boolean) => void;
}) {
  const c = connectionCopy(language);
  const [selected, setSelected] = useState("");
  const [preset, setPreset] = useState("openai_compatible");
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (requestedId) setSelected(requestedId); }, [requestedId]);
  useEffect(() => {
    if (!focusField || !["google_thinking_level", "anthropic_effort", "openai_reasoning_effort"].includes(focusField)) return;
    const compatible = Object.values(view.connections ?? {}).find(entry => focusField.startsWith(entry.connection.compatibility ?? "") || (focusField.startsWith("openai") && ["chat_completions", "responses", "azure"].includes(entry.connection.transport.kind ?? "")));
    if (compatible) setSelected(compatible.connection.id);
  }, [focusField, view.connections]);
  const draft = drafts[selected];
  const saved = view.connections?.[selected];
  const conn = draft?.connection ?? saved?.connection;
  useEffect(() => onDirty(Object.keys(drafts).length > 0), [drafts, onDirty]);
  function update(next: Partial<ModelConnection>, secrets?: Draft["credentials"]) {
    if (!conn) return;
    setDrafts(old => ({ ...old, [selected]: { connection: { ...conn, ...next },
      credentials: secrets ?? old[selected]?.credentials ?? {}, fresh: old[selected]?.fresh ?? false } }));
  }
  function discard() { setDrafts(old => { const next = { ...old }; delete next[selected]; return next; }); }
  async function save(action: ConnectionChange["action"] = draft?.fresh ? "create" : "update") {
    if (!conn) return;
    if (action === "delete" && !window.confirm(c.confirmDelete)) return;
    setBusy(true);
    try {
      const change: ConnectionChange = { action, id: conn.id };
      if (action === "create" || action === "update") Object.assign(change, {
        name: conn.name, preset: conn.preset, transport: conn.transport, compatibility: conn.compatibility,
        enabled: conn.enabled, discovery: conn.discovery, key_required: conn.key_required,
        reasoning_defaults: conn.reasoning_defaults, credentials: draft?.credentials ?? {},
      });
      const result = await api.saveSettings({ revision: view.revision, connection_changes: [change] });
      discard(); onSaved(result);
      if (action === "delete") setSelected("");
    } catch (cause) { onError(cause); } finally { setBusy(false); }
  }
  function create() {
    const source = schema.presets?.[preset];
    if (!source) return;
    const id = crypto.randomUUID();
    setDrafts(old => ({ ...old, [id]: { connection: { ...structuredClone(source), id, name: source.name }, credentials: {}, fresh: true } }));
    setSelected(id);
  }
  const entries = { ...Object.fromEntries(Object.entries(view.connections ?? {}).map(([id, v]) => [id, v.connection])),
    ...Object.fromEntries(Object.entries(drafts).map(([id, v]) => [id, v.connection])) };
  if (!active) return null;
  const transport = conn?.transport as Record<string, unknown> | undefined;
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
        {drafts[id] && <p>{c.dirty}</p>}<button className="button" onClick={() => setSelected(id)}>{c.edit}</button>
      </article>)}
    </div> : <div className="panel connection-editor">
      <header className="connection-editor-heading"><div><h3>{conn.name}</h3><p className="connection-address">{interfaceNames[conn.transport.kind ?? ""]}</p></div><button className="button" onClick={() => setSelected("")}>{c.back}</button></header>
      {!!saved?.missing_fields.length && <p className="alert">{c.missing}: {saved.missing_fields.map(field => connectionField(language, field)).join(", ")}</p>}
      <div className="configuration-grid">
        <label>{c.name}<input value={conn.name} onChange={e => update({ name: e.target.value })} /></label>
        <label className="checkbox-label"><input type="checkbox" checked={conn.enabled !== false} onChange={e => update({ enabled: e.target.checked })} />{c.enabled}</label>
        {Object.entries(transport ?? {}).filter(([key]) => key !== "kind").map(([key, value]) => <label key={key}>{connectionField(language, key)}
          {key === "auth_mode" ? <select value={String(value)} onChange={e => setTransport(key, e.target.value)}>{["system", "static", "bearer"].map(mode => <option key={mode}>{mode}</option>)}</select>
            : <input value={String(value ?? "")} onChange={e => setTransport(key, e.target.value || null)} />}</label>)}
      </div>
      <section className="connection-credentials"><h3>{c.credentials}</h3><p className="configuration-help">{text.keyHint}</p><div className="configuration-grid">
        {(conn.transport.kind === "bedrock" ? ["access_key_id", "secret_access_key", "session_token", "bearer_token"] : ["api_key"]).map(field => <CredentialEditor
          key={`${selected}-${field}-${view.revision}`} connectionId={selected} name={field} configured={saved?.credentials[field] ?? false}
          pending={draft?.credentials[field]} onChange={value => update({}, { ...draft?.credentials, [field]: value })} text={text} onError={onError} />)}
      </div></section>
      <details open={!!focusField || undefined}><summary>{c.advanced}</summary><div className="configuration-grid">
        {(["chat_completions", "responses"].includes(conn.transport.kind ?? "")) && <>
          <label>{c.protocol}<select value={conn.transport.kind} onChange={e => setTransport("kind", e.target.value)}><option value="chat_completions">Chat Completions</option><option value="responses">Responses</option></select></label>
          <label>{c.compatibility}<select value={conn.compatibility} onChange={e => update({ compatibility: e.target.value })}>{Object.entries(schema.presets ?? {}).filter(([, p]) => ["chat_completions", "responses"].includes(p.transport.kind ?? "")).map(([id, p]) => <option value={id} key={id}>{p.name}</option>)}</select></label>
          <label className="checkbox-label"><input type="checkbox" checked={conn.key_required ?? true} onChange={e => update({ key_required: e.target.checked })} />{c.required}</label>
        </>}
        {schema.fields.filter(f => f.group === "compatibility" && (f.key.startsWith(conn.transport.kind ?? "") || (f.key.startsWith("openai") && ["chat_completions", "responses", "azure"].includes(conn.transport.kind ?? "")))).map(f => <label key={f.key}>{f.label[language] ?? f.label.en}<select id={`setting-${f.key}`} value={conn.reasoning_defaults?.[f.key] ?? ""} onChange={e => update({ reasoning_defaults: { ...conn.reasoning_defaults, [f.key]: e.target.value || null } })}><option value="">{text.unset}</option>{f.options?.map(option => <option key={option}>{option}</option>)}</select></label>)}
      </div></details>
      <div className="configuration-actions sticky-save"><span>{draft && c.dirty}</span><button disabled={busy || !draft} className="button primary" onClick={() => void save()}>{text.save}</button><button className="button" disabled={busy || !draft} onClick={discard}>{text.cancel}</button>
        {!draft?.fresh && <><button className="button" title={c.resetHint} disabled={busy} onClick={() => void save("reset")}>{text.reset}</button><button className="button danger" disabled={busy} onClick={() => void save("delete")}>{c.delete}</button></>}
      </div>
    </div>}
  </section>;
}
