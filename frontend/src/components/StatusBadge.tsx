import { useTranslation } from "react-i18next";

const labels: Record<string, string> = {
  queued: "statusQueued",
  running: "statusRunning",
  succeeded: "statusSucceeded",
  failed: "statusFailed",
  cancelled: "statusCancelled",
  pending: "statusPending",
  resolved: "statusResolved",
};

export default function StatusBadge({ status }: { status: string }) {
  const { t } = useTranslation();
  return (
    <span className={`status status-${status}`}>
      <i />
      {labels[status] ? t(labels[status]) : status}
    </span>
  );
}
