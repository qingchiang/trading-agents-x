import cases from "./test-fixtures/numeric-display.json";
import { formatDecisionNumber } from "./numericDisplay";

describe("formatDecisionNumber", () => {
  it.each(cases)(
    "formats $value $unit for $language",
    ({ value, unit, language, expected }) => {
      expect(formatDecisionNumber(value, unit, language)).toBe(expected);
    },
  );
});
