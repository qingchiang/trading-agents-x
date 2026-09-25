import { lazy, Suspense, useId, useState } from "react";
import { useTranslation } from "react-i18next";

const JsonViewer = lazy(() => import("./JsonViewer"));

/** Closed records neither serialize payloads nor mount the code viewer. */
export default function JsonRecord({ label, value }: { label: string; value: unknown }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const id = useId();
  return <div className="json-record">
    <button type="button" className="text-button" aria-expanded={open} aria-controls={id} onClick={() => setOpen(current => !current)}>{t("rawRecord")}: {label}</button>
    {open && <div id={id}><Suspense fallback={<p role="status">{t("loading")}</p>}><JsonViewer value={value} label={label} /></Suspense></div>}
  </div>;
}
