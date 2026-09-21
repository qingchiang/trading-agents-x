import { connectionCopy } from "../settings/connectionCopy";

export default function ConnectionHistory({ snapshot, language, researchKind }: { researchKind?: string | null; snapshot?: Record<string, unknown> | null; language: string }) {
  const c = connectionCopy(language);
  const roles = (((researchKind ?? snapshot?.research_kind) === "incremental" ? ["deep"] : ["quick", "deep"]) as ("quick" | "deep")[]).flatMap(role => {
    const binding = snapshot?.[`${role}_binding`] as { model?: string; connection?: { name?: string; transport?: { base_url?: string; region?: string } } } | undefined;
    return binding?.connection ? [{ role, binding }] : [];
  });
  if (!roles.length) return null;
  return <section className="diagnostic-block"><h2>{c.connections}</h2><dl className="diagnostic-fields">
    {roles.map(({ role, binding }) => <div key={role}><dt>{c[role]}</dt><dd>{binding.connection?.name} · {binding.model}<br /><small>{binding.connection?.transport?.base_url ?? binding.connection?.transport?.region}</small></dd></div>)}
  </dl></section>;
}
