import { createPortal } from "react-dom";
import { useEffect, useState, type RefObject } from "react";
import { useTranslation } from "react-i18next";

/** Outline for structured views; Markdown readers supply their own stable anchors. */
export default function WorkspaceOutline({ container, target }: { container: RefObject<HTMLDivElement | null>; target: HTMLElement | null }) {
  const { t } = useTranslation();
  const [headings, setHeadings] = useState<{ id: string; label: string }[]>([]);
  useEffect(() => {
    const root = container.current;
    if (!root) return;
    let signature = "";
    const update = () => {
      const reader = root.querySelector(".workspace-reader");
      const elements = reader && !reader.querySelector(".report-reading-layout") ? [...reader.querySelectorAll<HTMLElement>("h2, h3")].filter(element => !element.closest("details:not([open])")) : [];
      const next = elements.map((element, index) => {
        if (!element.id) element.id = `research-section-${index}`;
        element.tabIndex = -1;
        return { id: element.id, label: element.textContent ?? "" };
      });
      const value = JSON.stringify(next);
      if (value !== signature) { signature = value; setHeadings(next); }
    };
    update();
    const observer = new MutationObserver(update);
    observer.observe(root, { childList: true, subtree: true, characterData: true });
    root.addEventListener("toggle", update, true);
    return () => { observer.disconnect(); root.removeEventListener("toggle", update, true); };
  }, [container]);
  if (!target || !headings.length) return null;
  return createPortal(<nav aria-label={t("onThisReport")} className="structured-outline floating-navigation-items">{headings.map(heading => <button key={heading.id} onClick={() => {
    const element = document.getElementById(heading.id);
    element?.scrollIntoView({ block: "start" }); element?.focus({ preventScroll: true });
    window.history.replaceState(null, "", `${window.location.pathname}${window.location.search}#${heading.id}`);
  }}>{heading.label}</button>)}</nav>, target);
}
