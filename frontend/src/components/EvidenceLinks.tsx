import { useTranslation } from "react-i18next";

import {
  groupEvidenceRefs,
  type EvidenceReferenceIndex,
} from "../evidence";

export default function EvidenceLinks({
  refs,
  evidenceIndex,
  onEvidence,
  label = true,
  compact = false,
  className = "",
}: {
  refs: string[];
  evidenceIndex: EvidenceReferenceIndex;
  onEvidence: (ref: string) => void;
  label?: boolean;
  compact?: boolean;
  className?: string;
}) {
  const { t } = useTranslation();
  const groups = groupEvidenceRefs(refs, evidenceIndex);
  if (groups.length === 0) return null;

  return (
    <div
      className={[
        "evidence-ref-group",
        compact ? "compact" : "",
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      {label && <strong>{t("evidenceRefs")}</strong>}
      <div className="evidence-chips">
        {groups.map((group) => (
          <span className="evidence-chip" key={group.alias}>
            <button
              type="button"
              className="open-evidence-button"
              onClick={() => onEvidence(group.targetRef)}
              aria-label={`${t("openEvidence")} ${group.targetRef}`}
              title={[...group.sources, ...group.refs].join("\n")}
            >
              <code>{group.alias}</code>
            </button>
            {!compact && (
              <button
                type="button"
                className="copy-chip-button"
                onClick={() => void copyEvidenceRef(group.targetRef)}
                aria-label={t("copyEvidenceId", {
                  ref: group.targetRef,
                })}
                title={group.targetRef}
              >
                ⧉
              </button>
            )}
          </span>
        ))}
      </div>
    </div>
  );
}

async function copyEvidenceRef(ref: string) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(ref);
    }
  } catch {
    // Clipboard permission failures do not affect evidence navigation.
  }
}
