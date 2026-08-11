import { useTranslation } from "react-i18next";

const labels: Record<string, string> = {
  queued: "statusQueued",
  running: "statusRunning",
  succeeded: "statusSucceeded",
  failed: "statusFailed",
  cancelled: "statusCancelled",
  pending: "statusPending",
  resolved: "statusResolved",
  awaiting_observation: "reviewLifecycle.awaiting_observation",
  observation_delayed: "reviewLifecycle.observation_delayed",
  awaiting_reflection: "reviewLifecycle.awaiting_reflection",
  reflection_retry_scheduled: "reviewLifecycle.reflection_retry_scheduled",
  reflection_failed: "reviewLifecycle.reflection_failed",
  reflection_invalid: "reviewLifecycle.reflection_invalid",
  feedback_available: "reviewLifecycle.feedback_available",
  feedback_ineligible: "reviewLifecycle.feedback_ineligible",
  feedback_retired: "reviewLifecycle.feedback_retired",
  lifecycle_inconsistent: "reviewLifecycle.lifecycle_inconsistent",
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
