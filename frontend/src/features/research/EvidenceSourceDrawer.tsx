import { formatResearchDate } from "../../shared/researchDate";
import { useTranslation } from "react-i18next";
import type { EvidenceReferenceIndex } from "./evidence";
import { useModal } from "../../shared/Interaction";
import Markdown from "../../shared/Markdown";
export default function EvidenceSourceDrawer({
  evidenceRef,
  evidenceIndex,
  onClose,
}: {
  evidenceRef: string | null;
  evidenceIndex: EvidenceReferenceIndex;
  onClose: () => void;
}) {
  const { t, i18n } = useTranslation();
  const group = evidenceRef
    ? evidenceIndex.groups.find((candidate) =>
        candidate.refs.includes(evidenceRef),
      )
    : undefined;

  const drawerRef = useModal<HTMLElement>(Boolean(evidenceRef), onClose);

  if (!evidenceRef) return null;
  return (
    <div className="source-drawer-layer" role="presentation">
      <button
        type="button"
        className="source-drawer-backdrop"
        aria-label={t("closeSourceDetails")}
        onClick={onClose}
      />
      <aside
        className="source-drawer"
        ref={drawerRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={t("sourceDetails")}
      >
        <header>
          <div>
            <p className="eyebrow">{t("sourceDetails")}</p>
            <h2>{group?.sources.join(", ") || t("unknownSource")}</h2>
          </div>
          <button type="button" className="button" onClick={onClose}>
            {t("close")}
          </button>
        </header>
        {!group ? (
          <div className="empty-state">{t("evidenceReferenceUnavailable")}</div>
        ) : (
          <>
            <dl className="evidence-metadata">
              <div>
                <dt>{t("evidenceOrigin")}</dt>
                <dd>
                  {group.origins
                    .map((origin) => t(`evidenceOrigin_${origin}`))
                    .join(", ")}
                </dd>
              </div>
              <div>
                <dt>{t("quality")}</dt>
                <dd>{t(`quality_${group.quality}`)}</dd>
              </div>
              <div>
                <dt>{t("effectiveDate")}</dt>
                <dd>{Array.from(new Set(group.items.flatMap(item => item.effective_date ? [formatResearchDate(item.effective_date, i18n.language)] : []))).join(", ") || "—"}</dd>
              </div>
              <div><dt>{t("availableAt")}</dt><dd>{formatResearchDate(group.canonical.available_at, i18n.language)}</dd></div>
              {(group.canonical.origins ?? []).map((origin, index) => <div key={index}><dt>{origin.source}</dt><dd>{t(`temporal_${origin.temporal_scope ?? "unknown"}`)}{origin.retrieved_at ? ` · ${formatResearchDate(origin.retrieved_at, i18n.language)}` : ""}</dd></div>) }

              <div>
                <dt>{t("fallback")}</dt>
                <dd>{group.fallback ? t("yes") : t("no")}</dd>
              </div>
            </dl>
            {group.canonical.content && (
              <div className="source-drawer-content">
                <Markdown>{group.canonical.content}</Markdown>
              </div>
            )}
            {group.canonical.value !== null &&
              group.canonical.value !== undefined && (
                <p className="source-drawer-value">
                  <strong>{t("value")}:</strong>{" "}
                  {String(group.canonical.value)}{" "}
                  {group.canonical.unit ?? ""}
                </p>
              )}

          </>
        )}
      </aside>
    </div>
  );
}
