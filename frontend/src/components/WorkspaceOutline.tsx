import { createPortal } from "react-dom";
import { useEffect, useState, type RefObject } from "react";
import { useTranslation } from "react-i18next";

/** Outline for structured views; Markdown readers supply their own stable anchors. */
export default function WorkspaceOutline({ container, target, onNavigate }: { onNavigate: () => void; container: RefObject<HTMLDivElement | null>; target: HTMLElement | null }) {
  const { t } = useTranslation();
  const [headings, setHeadings] = useState<{ id: string; label: string; level: number }[]>([]);
  const [active, setActive] = useState("");
  useEffect(() => {
    const root = container.current;
    if (!root) return;
    let signature = "";
    const update = () => {
      const reader = root.querySelector(".workspace-reader");
      const elements = reader && !reader.querySelector("[data-report-outline], [data-process-outline]")
        ? [...reader.querySelectorAll<HTMLElement>("[data-outline][id]")].filter(element => !element.closest(".source-drawer-layer")) : [];
      const next = elements.map(element => {
        element.tabIndex = -1;
        return { id: element.id, label: element.dataset.outline ?? "", level: Number(element.dataset.outlineLevel ?? 2) };
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
  useEffect(() => {
    const update = () => {
      let next = headings[0]?.id ?? "";
      for (const heading of headings) if ((document.getElementById(heading.id)?.getBoundingClientRect().top ?? Infinity) <= 140) next = heading.id;
      setActive(next);
    };
    update(); window.addEventListener("scroll", update, { passive: true });
    return () => window.removeEventListener("scroll", update);
  }, [headings]);
  if (!target || !headings.length) return null;
  return createPortal(<nav aria-label={t("onThisReport")} className="structured-outline floating-navigation-items">{headings.map(heading => <button key={heading.id} data-level={heading.level} aria-current={active === heading.id ? "location" : undefined} onClick={() => {
    onNavigate();
    const element = document.getElementById(heading.id);
    if (element instanceof HTMLDetailsElement) element.open = true;
    element?.scrollIntoView?.({ block: "start" }); element?.focus({ preventScroll: true });
    window.history.replaceState(window.history.state, "", `${window.location.pathname}${window.location.search}#${heading.id}`);
  }}>{heading.label}</button>)}</nav>, target);
}
