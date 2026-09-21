import { useLayoutEffect, useRef, useState, type ReactNode } from "react";

/** Anchor to the settings content, including sidebar transitions and safe areas. */
export default function SettingsSaveBar({ label, children }: { label: string; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  const [box, setBox] = useState({ left: 0, width: 0, height: 120 });
  useLayoutEffect(() => {
    const bar = ref.current;
    const page = bar?.closest<HTMLElement>(".configuration-page");
    if (!bar || !page) return;
    const previousPadding = document.documentElement.style.scrollPaddingBottom;
    const previousPagePadding = page.style.paddingBottom;
    const measure = () => {
      const rect = page.getBoundingClientRect();
      const height = bar.getBoundingClientRect().height + 24;
      document.documentElement.style.scrollPaddingBottom = `${height}px`;
      page.style.paddingBottom = `${height}px`;
      setBox({ left: rect.left, width: rect.width, height });
    };
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(page); observer?.observe(bar);
    const revealFocus = (event: FocusEvent) => {
      const target = event.target;
      if (!(target instanceof HTMLElement) || bar.contains(target)) return;
      const bottom = target.getBoundingClientRect().bottom;
      const top = bar.getBoundingClientRect().top;
      if (bottom > top - 16) target.scrollIntoView?.({ block: "center" });
    };
    page.addEventListener("focusin", revealFocus);
    window.addEventListener("resize", measure);
    return () => { observer?.disconnect(); window.removeEventListener("resize", measure); page.removeEventListener("focusin", revealFocus); document.documentElement.style.scrollPaddingBottom = previousPadding; page.style.paddingBottom = previousPagePadding; };
  }, []);
  return <div ref={ref} role="region" aria-label={label} className="configuration-actions settings-save-bar" style={{ left: box.left, width: box.width || undefined }}><span>{label}</span>{children}</div>;
}
