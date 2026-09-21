const FIAT_UNITS = new Set([
  "$",
  "¥",
  "AUD",
  "CAD",
  "CHF",
  "CNY",
  "EUR",
  "GBP",
  "HKD",
  "JPY",
  "KRW",
  "USD",
  "円",
]);
const PERCENT_UNITS = new Set(["%", "PCT", "PERCENT"]);
const RATIO_UNITS = new Set(["X", "倍"]);

export function decisionFractionDigits(value: number, unit?: string): number {
  const normalized = (unit ?? "").trim().toUpperCase();
  const absolute = Math.abs(value);
  if (PERCENT_UNITS.has(normalized) || RATIO_UNITS.has(normalized)) return 2;
  if (absolute > 0 && absolute < 1) {
    const magnitude = Math.floor(Math.log10(absolute));
    return Math.min(8, Math.max(0, 3 - magnitude));
  }
  if (FIAT_UNITS.has(normalized)) return 2;
  return 4;
}

export function decisionNumberLocale(language?: string): string {
  const normalized = (language ?? "").toLowerCase();
  if (normalized.startsWith("zh")) return "zh-CN";
  if (normalized.startsWith("ja")) return "ja-JP";
  return "en-US";
}

export function formatDecisionNumber(
  value: number,
  unit?: string,
  language?: string,
): string {
  if (!Number.isFinite(value)) return String(value);
  const digits = decisionFractionDigits(value, unit);
  return new Intl.NumberFormat(decisionNumberLocale(language), {
    useGrouping: true,
    minimumFractionDigits: 0,
    maximumFractionDigits: digits,
  }).format(Object.is(value, -0) ? 0 : value);
}
