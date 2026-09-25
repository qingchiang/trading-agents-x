import { useTranslation } from "react-i18next";
import { connectionCopy } from "../settings/connectionCopy";

export default function ConnectionHistory({ snapshot, language, researchKind }: { researchKind?: string | null; snapshot?: Record<string, unknown> | null; language: string }) {
  const c = connectionCopy(language);
  const {t} = useTranslation();
  const roles = (((researchKind ?? snapshot?.research_kind) === "incremental" ? ["deep"] : ["quick", "deep"]) as ("quick" | "deep")[]).map(role => {
    const binding = snapshot?.[`${role}_binding`] as { model?: string; connection?: { name?: string; transport?: { base_url?: string; region?: string } } } | undefined;
    return { role, binding };
  });
  return <section className="diagnostic-block"><h2>{c.connections}</h2><dl className="diagnostic-fields">
    {roles.map(({ role, binding }) => <div key={role}><dt>{c[role]}</dt><dd>{binding?.connection?.name ?? t("notRecorded")} · {binding?.model ?? t("notRecorded")}<br /><small>{binding?.connection?.transport?.base_url ?? binding?.connection?.transport?.region}</small></dd></div>)}
  </dl></section>;
}
