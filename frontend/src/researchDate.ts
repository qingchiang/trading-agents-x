/** Calendar cutoffs stay on their recorded date; instants retain an explicit UTC label. */
export function formatResearchDate(value: string | null | undefined, language: string): string {
  if (!value) return "—";
  const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(value);
  const date = new Date(dateOnly ? `${value}T00:00:00Z` : value);
  if (!Number.isFinite(date.getTime())) return value;
  return new Intl.DateTimeFormat(language, {
    year: "numeric", month: "short", day: "numeric", timeZone: "UTC",
    ...(dateOnly ? {} : { hour: "2-digit", minute: "2-digit", timeZoneName: "short" as const }),
  }).format(date);
}
