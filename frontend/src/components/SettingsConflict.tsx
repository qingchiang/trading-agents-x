import { useState } from "react";
import type { Choices, FieldConflict } from "../settingsDraft";
import { editingCopy } from "../settingsEditingCopy";

export function DisplayValue({ value }: { value: unknown }) {
  if (value === undefined || value === null) return <>—</>;
  if (Array.isArray(value)) return <>{value.map(v => String(v)).join(" → ") || "—"}</>;
  if (typeof value === "object") return <dl>{Object.entries(value).map(([k, v]) => <div key={k}><dt>{k}</dt><dd><DisplayValue value={v} /></dd></div>)}</dl>;
  return <>{String(value)}</>;
}
export default function SettingsConflict({ conflicts, credentials = {}, language, label, onApply }: {
  conflicts: FieldConflict[]; credentials?: Record<string, "replace" | "remove">; language: string;
  label: (path: string[]) => string; onApply: (choices: Choices, credentials: Choices) => void;
}) {
  const c = editingCopy(language);
  const [choices, setChoices] = useState<Choices>({});
  const [secretChoices, setSecretChoices] = useState<Choices>({});
  const ready = conflicts.every(row => choices[JSON.stringify(row.path)]) && Object.keys(credentials).every(key => secretChoices[key]);
  return <section className="settings-conflict" role="alert" aria-label={c.title}>
    <h3>{c.title}</h3><p>{c.hint}</p>
    {conflicts.map(row => { const key = JSON.stringify(row.path); return <fieldset key={key}>
      <legend>{label(row.path)}</legend><div className="conflict-values">{(["base", "server", "local"] as const).map(side => <div key={side}><strong>{c[side]}</strong><DisplayValue value={row[side]} /></div>)}</div>
      <div className="configuration-actions">{(["server", "local"] as const).map(side => <label className="checkbox-label" key={side}><input type="radio" name={`conflict-${key}`} checked={choices[key] === side} onChange={() => setChoices(old => ({ ...old, [key]: side }))} />{c[`${side}Choice`]}</label>)}</div>
    </fieldset>; })}
    {!!Object.keys(credentials).length && <><h4>{c.credentials}</h4><p>{c.credentialHint}</p>{Object.entries(credentials).map(([key, operation]) => <fieldset key={key}><legend>{label([key])} · {c[operation]}</legend>{(["local", "server"] as const).map(side => <label className="checkbox-label" key={side}><input type="radio" name={`credential-conflict-${key}`} checked={secretChoices[key] === side} onChange={() => setSecretChoices(old => ({ ...old, [key]: side }))} />{side === "local" ? c.keepCredential : c.cancelCredential}</label>)}</fieldset>)}</>}
    <button className="button primary" disabled={!ready} onClick={() => onApply(choices, secretChoices)}>{c.apply}</button>
  </section>;
}
