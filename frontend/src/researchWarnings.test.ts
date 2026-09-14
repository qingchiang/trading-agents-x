import { expect, test } from "vitest";
import { numericWarningLabel } from "./researchWarnings";

test("only consolidates recognized aggregate numerical limitations", () => {
  const warning = { code: "decision.numeric_audit_partial", message: "Optional numeric components were omitted because their audit failed. The qualitative decision remains audited." };
  expect(numericWarningLabel(warning)).toBe("numericAuditPartial");
  expect(numericWarningLabel({ ...warning, evidence_ref: "ev_0123456789ab" })).toBeNull();
  expect(numericWarningLabel({ ...warning, message: "A distinct new limitation" })).toBeNull();
  expect(numericWarningLabel({ ...warning, code: "unknown" })).toBeNull();
});
