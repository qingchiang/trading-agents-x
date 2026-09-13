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
      const elements = reader && !reader.querySelector(".report-reading-layout[data-report-outline]") ? [...reader.querySelectorAll<HTMLElement>("h1, h2, h3")].filter(element => !element.closest("details:not([open]), .reading-toolbar, .source-drawer-layer")) : [];
      const next = elements.map((element, index) => {
        if (!element.id) element.id = `research-section-${index}`;
        element.tabIndex = -1;
        return { id: element.id, label: element.textContent ?? "", level: Number(element.tagName.slice(1)) };
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
    element?.scrollIntoView({ block: "start" }); element?.focus({ preventScroll: true });
    window.history.replaceState(window.history.state, "", `${window.location.pathname}${window.location.search}#${heading.id}`);
  }}>{heading.label}</button>)}</nav>, target);
}
