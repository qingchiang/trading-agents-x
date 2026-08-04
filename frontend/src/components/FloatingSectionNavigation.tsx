import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

export type SectionNavigationEntry = {
  id: string;
  label: string;
};

export default function FloatingSectionNavigation({
  entries,
  active,
  title,
  ariaLabel,
  selectLabel,
  storageKey,
  ariaCurrent = "location",
  onSelect,
}: {
  entries: SectionNavigationEntry[];
  active: string;
  title: string;
  ariaLabel: string;
  selectLabel: string;
  storageKey: string;
  ariaCurrent?: "location" | "step";
  onSelect: (id: string) => void;
}) {
  const { t } = useTranslation();
  const slotRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(() => readOpenState(storageKey));
  const [external, setExternal] = useState(false);

  useEffect(() => {
    const slot = slotRef.current;
    const reader = slot?.parentElement;
    const sidebar = document.querySelector<HTMLElement>(".sidebar");
    if (!slot || !reader) return;
    const update = () => {
      const readerLeft = reader.getBoundingClientRect().left;
      const sidebarRight = sidebar?.getBoundingClientRect().right ?? 0;
      setExternal(readerLeft - sidebarRight >= 218);
    };
    update();
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(update);
    observer?.observe(reader);
    if (sidebar) observer?.observe(sidebar);
    window.addEventListener("resize", update);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", update);
    };
  }, []);

  if (entries.length === 0) return null;
  const toggle = () => {
    const next = !open;
    setOpen(next);
    sessionStorage.setItem(storageKey, next ? "open" : "closed");
  };

  return (
    <>
      <div className="floating-navigation-slot" ref={slotRef}>
        <div
          className={`floating-section-navigation ${open ? "open" : "collapsed"} ${
            external ? "external" : "overlay"
          }`}
        >
          {open ? (
            <nav aria-label={ariaLabel}>
              <header>
                <strong>{title}</strong>
                <button
                  type="button"
                  className="floating-navigation-toggle"
                  aria-label={t("closeNavigation")}
                  aria-expanded="true"
                  onClick={toggle}
                >
                  <span aria-hidden="true">‹</span>
                </button>
              </header>
              <div className="floating-navigation-items">
                {entries.map((entry) => (
                  <button
                    type="button"
                    className={active === entry.id ? "active" : ""}
                    aria-current={active === entry.id ? ariaCurrent : undefined}
                    onClick={() => onSelect(entry.id)}
                    key={entry.id}
                  >
                    {entry.label}
                  </button>
                ))}
              </div>
            </nav>
          ) : (
            <button
              type="button"
              className="floating-navigation-trigger"
              aria-label={t("openNavigation")}
              aria-expanded="false"
              onClick={toggle}
            >
              <span aria-hidden="true">☰</span>
            </button>
          )}
        </div>
      </div>
      <label className="floating-section-select">
        <span>{selectLabel}</span>
        <select value={active} onChange={(event) => onSelect(event.target.value)}>
          {entries.map((entry) => (
            <option value={entry.id} key={entry.id}>
              {entry.label}
            </option>
          ))}
        </select>
      </label>
    </>
  );
}

function readOpenState(storageKey: string): boolean {
  return sessionStorage.getItem(storageKey) !== "closed";
}
