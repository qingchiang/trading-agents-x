const DAY_MILLISECONDS = 24 * 60 * 60 * 1000;

export type TrashDeadline = {
  deletionAt: Date;
  remainingDays: number;
  due: boolean;
};

export function trashDeadline(
  trashedAt: string | Date,
  retentionDays: number,
  now = new Date(),
): TrashDeadline | null {
  if (retentionDays === 0) return null;
  const deletionAt = new Date(trashedAt);
  deletionAt.setUTCDate(deletionAt.getUTCDate() + retentionDays);
  const remainingMilliseconds = deletionAt.getTime() - now.getTime();
  return {
    deletionAt,
    remainingDays: Math.max(
      0,
      Math.ceil(remainingMilliseconds / DAY_MILLISECONDS),
    ),
    due: remainingMilliseconds <= 0,
  };
}

export function formatUtcDate(value: Date): string {
  return new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "long",
    day: "2-digit",
    timeZone: "UTC",
  }).format(value);
}
