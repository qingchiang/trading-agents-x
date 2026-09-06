import WorkspaceOutline from "./WorkspaceOutline";
import { createContext, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { tabsKeyDown, useModal } from "./Interaction";

export const WorkspaceNavigationTarget = createContext<HTMLElement | null | undefined>(undefined);

export default function ResearchWorkspace({ history, children }: { history: ReactNode; children: ReactNode }) {
  const { t } = useTranslation();
  const container = useRef<HTMLDivElement>(null);
  const [wide, setWide] = useState(false);
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState("history");
  const [target, setTarget] = useState<HTMLDivElement | null>(null);
  const targetRef = useCallback((element: HTMLDivElement | null) => setTarget(element), []);
  const panel = useModal<HTMLElement>(open && !wide, () => setOpen(false));
  useEffect(() => {
    if (!container.current || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      setWide(entry.contentRect.width >= 1100);
      setOpen(false);
    });
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);
  return <WorkspaceNavigationTarget.Provider value={target}>
    <div className={`research-workspace ${wide ? "wide" : "compact"}`} ref={container}>
      {!wide && <button className="button workspace-navigation-trigger" aria-expanded={open} onClick={() => setOpen(true)}>{t("historyNavigation")} / {t("onThisReport")}</button>}
      {!wide && open && <div className="workspace-scrim" onClick={() => setOpen(false)} />}
      <aside className="workspace-auxiliary" hidden={!wide && !open} ref={panel} role={!wide && open ? "dialog" : undefined} aria-modal={!wide && open || undefined} aria-label={t("historyNavigation")}>
        {!wide && <button className="button" onClick={() => setOpen(false)}>{t("closeNavigation")}</button>}
        <div className="auxiliary-tabs" role="tablist" onKeyDown={tabsKeyDown}>
          {["history", "contents"].map(value => <button role="tab" aria-selected={tab === value} tabIndex={tab === value ? 0 : -1} onClick={() => setTab(value)} key={value}>{t(value === "history" ? "historyNavigation" : "onThisReport")}</button>)}
        </div>
        <div hidden={tab !== "history"} onClick={event => { if ((event.target as HTMLElement).closest(".history-select")) setOpen(false); }}>{history}</div>
        <div hidden={tab !== "contents"} className="workspace-contents" ref={targetRef} onClick={event => { if ((event.target as HTMLElement).closest(".floating-navigation-items button")) setOpen(false); }} />
      </aside>
      {children}
      <WorkspaceOutline container={container} target={target} />
    </div>
  </WorkspaceNavigationTarget.Provider>;
}
