import type { ConfigurationSchema, ConfigurationView } from "../../shared/api/client";
import { connectionCopy, connectionField } from "./connectionCopy";
import { Link } from "../../app/router";

export default function SettingsSearch({ query, schema, view, language, onNavigate }: {
  query: string; schema: ConfigurationSchema; view: ConfigurationView; language: string; onNavigate: (group: string) => void;
}) {
  if (!query) return null;
  const matches = (...words: unknown[]) => words.join(" ").toLowerCase().includes(query.toLowerCase());
  const c = connectionCopy(language);
  const results: { id: string; label: string; key: string; target: string; group: string }[] = [];
  for (const field of schema.fields) {
    if (!matches(field.key, ...Object.values(field.label), ...field.env_names ?? [])) continue;
    const category = field.group === "cache" ? "storage" : ["sources", "news"].includes(field.group) ? "data" : "research";
    results.push({ id: field.key, label: field.label[language] ?? field.label.en, key: field.key, target: `/settings/${category}#setting-${field.key}`, group: field.group });
  }
  for (const [name, owner] of Object.entries(schema.credential_owners)) {
    if (owner in schema.providers) continue;
    const metadata = schema.credential_metadata?.[name];
    if (matches(name, owner, metadata?.label, ...Object.values(metadata?.description ?? {}))) results.push({ id: name, label: metadata?.label ?? owner, key: name, target: `/settings/data#credential-${name}`, group: "sources" });
  }
  for (const { connection: conn } of Object.values(view.connections ?? {})) {
    const target = `/settings/connections?connection=${encodeURIComponent(conn.id)}`;
    if (matches(conn.id, conn.name, conn.preset, "models connections")) results.push({ id: conn.id, label: conn.name, key: "", target: `${target}#connection-name`, group: "connections" });
    for (const field of ["name", "enabled", ...Object.keys(conn.transport), "compatibility", "key_required", "discovery"]) {
      if (["kind", "compatibility", "key_required"].includes(field) && !["chat_completions", "responses"].includes(conn.transport.kind ?? "")) continue;
      const labels = ["en", "zh-CN", "ja"].map(lang => connectionField(lang, field));
      if (matches(field, ...labels)) results.push({ id: `${conn.id}-${field}`, label: `${conn.name} · ${connectionField(language, field)}`, key: field, target: `${target}#connection-${field}`, group: "connections" });
    }
    const credentialFields = conn.transport.kind === "bedrock" ? ["access_key_id", "secret_access_key", "session_token", "bearer_token"] : ["api_key"];
    for (const field of credentialFields) {
      if (matches(field, ...["en", "zh-CN", "ja"].map(lang => connectionField(lang, field)))) results.push({ id: `${conn.id}-credential-${field}`, label: `${conn.name} · ${connectionField(language, field)}`, key: field, target: `${target}#credential-${field}`, group: "connections" });
    }
  }
  return <div className="panel configuration-search-results"><h2>{c.results}</h2>{results.map(item => <Link key={item.id} to={item.target} onClick={() => onNavigate(item.group)}>{item.label}<code>{item.key}</code></Link>)}</div>;
}
