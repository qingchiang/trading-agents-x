import { useTranslation } from "react-i18next";

export default function ResearchKindBadge({ kind }: {
  kind?: "full" | "incremental" | null;
  request?: object | null;
  methodSnapshot?: Record<string, unknown> | null;
}) {
  const { t } = useTranslation();
  const normalized = kind === "incremental" ? "incremental" : "full";
  return <span className={`research-kind-badge ${normalized}`}>{t(normalized === "incremental" ? "incrementalResearch" : "fullResearch")}</span>;
}
