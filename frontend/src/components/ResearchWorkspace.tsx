import Icon from "./Icon";
import WorkspaceOutline from "./WorkspaceOutline";
import { createContext, useContext, useCallback, useEffect, useLayoutEffect, useId, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { tabsKeyDown, useModal } from "./Interaction";

export const WorkspaceNavigationActions = createContext<{ open: (tab: string) => void; close: () => void; compact: boolean; registerToolbar: (present: boolean) => void } | null>(null);

export const WorkspaceNavigationTarget = createContext<HTMLElement | null | undefined>(undefined);

export default function ResearchWorkspace({ history, children }: { history: ReactNode; children: ReactNode }) {
  const { t } = useTranslation();
  const tabsId = useId();
  const container = useRef<HTMLDivElement>(null);
  const [hasToolbar, setHasToolbar] = useState(false);
  const [wide, setWide] = useState(false);
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState("history");
  const [target, setTarget] = useState<HTMLDivElement | null>(null);
  const targetRef = useCallback((element: HTMLDivElement | null) => setTarget(element), []);
  const panel = useModal<HTMLElement>(open && !wide, () => setOpen(false));
  useEffect(() => {
    if (!container.current || typeof ResizeObserver === "undefined") return;
    let previousWide: boolean | undefined;
    const observer = new ResizeObserver(([entry]) => {
      const nextWide = entry.contentRect.width >= 1100;
      setWide(nextWide);
      if (previousWide !== nextWide) setOpen(false);
      previousWide = nextWide;
    });
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);
  useLayoutEffect(() => {
    if (!wide && !open || tab !== "history") return;
    const frame = requestAnimationFrame(() => {
      const rail = panel.current;
      const selected = rail?.querySelector<HTMLElement>(".history-select[aria-current]");
      if (!rail || !selected) return;
      const area = rail.getBoundingClientRect();
      const row = selected.getBoundingClientRect();
      if (row.top < area.top + 70) rail.scrollTop += row.top - area.top - 70;
      else if (row.bottom > area.bottom - 16) rail.scrollTop += row.bottom - area.bottom + 16;
    });
    return () => cancelAnimationFrame(frame);
  }, [wide, open, tab, history, panel]);
  return <WorkspaceNavigationActions.Provider value={{ open: value => { setTab(value); setOpen(true); }, close: () => setOpen(false), compact: !wide, registerToolbar: setHasToolbar }}>
    <WorkspaceNavigationTarget.Provider value={target}>
    <div className={`research-workspace ${wide ? "wide" : "compact"}`} ref={container}>
      {!wide && open && <div className="workspace-scrim" onClick={() => setOpen(false)} />}
      <aside className="workspace-auxiliary" hidden={!wide && !open} ref={panel} role={!wide && open ? "dialog" : undefined} aria-modal={!wide && open || undefined} aria-label={t("historyNavigation")}>
        {!wide && <button className="button" onClick={() => setOpen(false)}>{t("closeNavigation")}</button>}
        <div className="auxiliary-tabs" role="tablist" onKeyDown={tabsKeyDown}>
          {["history", "contents"].map(value => <button role="tab" id={`${tabsId}-${value}`} aria-controls={`${tabsId}-${value}-panel`} aria-selected={tab === value} tabIndex={tab === value ? 0 : -1} onClick={() => setTab(value)} key={value}>{t(value === "history" ? "historyNavigation" : "onThisReport")}</button>)}
        </div>
        <div role="tabpanel" id={`${tabsId}-history-panel`} aria-labelledby={`${tabsId}-history`} hidden={tab !== "history"} onClick={event => { if ((event.target as HTMLElement).closest(".history-select")) setOpen(false); }}>{history}</div>
        <div role="tabpanel" id={`${tabsId}-contents-panel`} aria-labelledby={`${tabsId}-contents`} hidden={tab !== "contents"} className="workspace-contents" ref={targetRef} onClick={event => { if ((event.target as HTMLElement).closest(".floating-navigation-items button")) setOpen(false); }} />
      </aside>
      {!wide && !hasToolbar && <div className="workspace-fallback-navigation"><button className="button" aria-expanded={open} onClick={() => { setTab("history"); setOpen(true); }}>{t("historyNavigation")}</button></div>}
      {children}
      <WorkspaceOutline container={container} target={target} onNavigate={() => setOpen(false)} />
    </div>
  </WorkspaceNavigationTarget.Provider></WorkspaceNavigationActions.Provider>;
}

export function WorkspaceNavigationButtons() {
  const { t } = useTranslation();
  const navigation = useContext(WorkspaceNavigationActions);
  const register = navigation?.registerToolbar;
  useEffect(() => { register?.(true); return () => register?.(false); }, [register]);
  if (!navigation?.compact) return null;
  return <div className="reading-navigation-actions">
    <button className="button" aria-label={t("historyNavigation")} title={t("historyNavigation")} onClick={() => navigation.open("history")}><Icon name="history" /><span>{t("historyNavigation")}</span></button>
    <button className="button" aria-label={t("onThisReport")} title={t("onThisReport")} onClick={() => navigation.open("contents")}><Icon name="menu" /><span>{t("onThisReport")}</span></button>
  </div>;
}
