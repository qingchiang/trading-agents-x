import { createContext } from "react";

export const NumericNoticeHandled = createContext(false);

/** Only recognized aggregate warnings may replace the numeric status notice. */
export function numericWarningLabel(warning: string | { code: string; message: string; evidence_ref?: string | null }): string | null {
  if (typeof warning === "string" || warning.evidence_ref) return null;
  const messages = new Set([
    "Optional numeric components were omitted because their audit failed. The qualitative decision remains audited.",
    "Decision-critical numeric annotations were incomplete; the qualitative decision was retained and unverified calculations were omitted.",
  ]);
  if (!messages.has(warning.message)) return null;
  return warning.code === "decision.numeric_audit_partial" ? "numericAuditPartial"
    : warning.code === "decision.numeric_audit_incomplete" ? "numericAuditIncomplete" : null;
}
