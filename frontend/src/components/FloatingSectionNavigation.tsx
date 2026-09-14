import Icon from "./Icon";
import { createPortal } from "react-dom";
import { WorkspaceNavigationActions, WorkspaceNavigationTarget } from "./ResearchWorkspace";
import { useContext, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

export type SectionNavigationEntry = {
  id: string;
  label: string;
  level?: number;
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
  const navigation = useContext(WorkspaceNavigationActions);
  const workspaceTarget = useContext(WorkspaceNavigationTarget);
  const slotRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(() => readOpenState(storageKey));


  if (entries.length === 0) return null;
  const toggle = () => {
    const next = !open;
    setOpen(next);
    sessionStorage.setItem(storageKey, next ? "open" : "closed");
  };

  if (workspaceTarget !== undefined) return workspaceTarget ? createPortal(
    <nav aria-label={ariaLabel} className="floating-navigation-items">{entries.map(entry => <button
      type="button" key={entry.id} className={active === entry.id ? "active" : ""}
      data-level={entry.level ?? 2} aria-current={active === entry.id ? ariaCurrent : undefined}
      onClick={() => { onSelect(entry.id); navigation?.close(); }}>{entry.label}</button>)}</nav>, workspaceTarget) : null;

  const content = (
    <>
      <div className="floating-navigation-slot" ref={slotRef}>
        <div
          className={`floating-section-navigation ${open ? "open" : "collapsed"} `}
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
                  <Icon name="close" />
                </button>
              </header>
              <div className="floating-navigation-items">
                {entries.map((entry) => (
                  <button
                    type="button"
                    className={active === entry.id ? "active" : ""}
                    aria-current={active === entry.id ? ariaCurrent : undefined}
                    onClick={() => { onSelect(entry.id); navigation?.close(); }}
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
              <Icon name="menu" />
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
  return content;
}

function readOpenState(storageKey: string): boolean {
  return sessionStorage.getItem(storageKey) !== "closed";
}
