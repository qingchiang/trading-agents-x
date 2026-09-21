import { useState } from "react";
import { api, type ConnectionView, type ProviderModelCatalog } from "../../shared/api/client";
import { connectionCopy, reasoningLabel } from "./connectionCopy";

export type RoleSelection = { connection: string; model: string; reasoning: string };
export type RoleSelections = { quick: RoleSelection; deep: RoleSelection };
export default function RoleConnections({ connections, value, onChange, language, includeQuick = true }: {
  connections: Record<string, ConnectionView>; value: RoleSelections; onChange: (next: RoleSelections) => void; language: string; includeQuick?: boolean;
}) {
  const c = connectionCopy(language);
  const [separate, setSeparate] = useState(value.quick.connection !== value.deep.connection);
  const [merging, setMerging] = useState(false);
  const [catalogs, setCatalogs] = useState<Record<string, ProviderModelCatalog>>({});
  const [warning, setWarning] = useState("");
  const [loading, setLoading] = useState<string | null>(null);
  const split = separate || value.quick.connection !== value.deep.connection;
  const choose = (role: "quick" | "deep", id: string, both = false) => {
    const next = { ...value };
    for (const key of (both ? ["quick", "deep"] : [role]) as ("quick" | "deep")[]) {
      next[key] = value[key].connection === id ? value[key] : { connection: id, model: "", reasoning: "provider_default" };
    }
    onChange(next);
  };
  const selector = (role: "quick" | "deep", both = false) => <label>{both ? c.common : c[role]}
    <select id={`setting-${role}_connection_id`} value={value[role].connection} onChange={e => choose(role, e.target.value, both)}>
      <option value="">{c.choose}</option>
      {value[role].connection && !connections[value[role].connection]?.selectable && <option value={value[role].connection} disabled>{connections[value[role].connection]?.connection.name ?? value[role].connection} — {c.unavailable}</option>}
      {Object.values(connections).filter(entry => entry.selectable).map(entry => <option key={entry.connection.id} value={entry.connection.id}>{entry.connection.name}</option>)}
    </select>
  </label>;
  async function refresh(identity: string) {
    setLoading(identity); setWarning("");
    try { const catalog = await api.connectionModels(identity, true); setCatalogs(old => ({ ...old, [identity]: catalog })); setWarning(catalog.warning?.message ?? ""); }
    catch (cause) { setWarning(cause instanceof Error ? cause.message : String(cause)); }
    finally { setLoading(null); }
  }
  return <div className="role-connections">
    {includeQuick && <label className="checkbox-label"><input type="checkbox" checked={!split && !merging} onChange={e => {
      if (e.target.checked && value.quick.connection !== value.deep.connection) setMerging(true);
      else { setSeparate(!e.target.checked); setMerging(false); }
    }} />{c.shared}</label>}
    {merging && <label>{c.keep}<select defaultValue="" onChange={e => { if (!e.target.value) return; choose("deep", e.target.value, true); setMerging(false); setSeparate(false); }}><option value="">{c.choose}</option>{Array.from(new Set([value.quick.connection, value.deep.connection])).filter(Boolean).map(id => <option key={id} value={id}>{connections[id]?.connection.name ?? id}</option>)}</select></label>}
    {includeQuick && !split && selector("deep", true)}
    <div className="configuration-grid">
      {((includeQuick ? ["quick", "deep"] : ["deep"]) as ("quick" | "deep")[]).map(role => {
        const selection = value[role]; const catalog = catalogs[selection.connection];
        const options = catalog?.models.find(model => model.id === selection.model)?.reasoning_efforts ?? connections[selection.connection]?.reasoning_efforts ?? ["provider_default"];
        return <fieldset className="model-group" key={role}><legend>{c[role]}</legend>
          {(split || !includeQuick) && selector(role)}
          <label>{c.manual}<input id={`setting-${role}_think_llm`} list={`role-models-${role}`} value={selection.model} onChange={e => onChange({ ...value, [role]: { ...selection, model: e.target.value, reasoning: "provider_default" } })} /></label>
          <datalist id={`role-models-${role}`}>{catalog?.models.map(model => <option key={model.id} value={model.id} />)}</datalist>
          <label>{language.startsWith("zh") ? "推理强度" : language.startsWith("ja") ? "推論強度" : "Reasoning effort"}<select id={`setting-${role}_reasoning_effort`} value={selection.reasoning} onChange={e => onChange({ ...value, [role]: { ...selection, reasoning: e.target.value } })}><option value="">{language.startsWith("zh") ? "继承连接设置" : language.startsWith("ja") ? "接続設定を継承" : "Inherit connection"}</option>{Array.from(new Set([...options, selection.reasoning])).filter(Boolean).map(option => <option key={option} value={option}>{reasoningLabel(language, option)}</option>)}</select></label>
          <button className="button" type="button" disabled={!selection.connection || loading !== null} onClick={() => void refresh(selection.connection)}>{c.refresh}</button>
          {selection.connection && !connections[selection.connection]?.selectable && <p role="alert">{c.unavailable}</p>}
        </fieldset>;
      })}
    </div>{warning && <p role="status">{warning}</p>}
  </div>;
}
