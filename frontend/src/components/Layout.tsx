import { PropsWithChildren, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import Icon from "./Icon";
import { useModal } from "./Interaction";
import i18n from "../i18n";
import { Link, usePathname } from "../router";

const sidebarPreferenceKey = "tradingagents-sidebar-collapsed";
const nav = [
  { to: "/", key: "dashboard" },
  { to: "/timelines", key: "researchTimelines" },
  { to: "/runs", key: "runManagement" },
  { to: "/settings", key: "settings" },
];

export default function Layout({ children }: PropsWithChildren) {
  const { t } = useTranslation();
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(
    () => localStorage.getItem(sidebarPreferenceKey) === "true",
  );
  const [drawerOpen, setDrawerOpen] = useState(false);
  const sidebarRef = useModal<HTMLElement>(drawerOpen, () => setDrawerOpen(false));

  useEffect(() => {
    if (!drawerOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDrawerOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [drawerOpen]);

  useEffect(() => {
    const refresh = () => setCollapsed(localStorage.getItem(sidebarPreferenceKey) === "true");
    window.addEventListener("tradingagents:preferences", refresh);
    return () => window.removeEventListener("tradingagents:preferences", refresh);
  }, []);
  const changeLocale = (locale: string) => {
    localStorage.setItem("tradingagents-locale", locale);
    void i18n.changeLanguage(locale);
  };
  const toggleCollapsed = () => {
    const next = !collapsed;
    setCollapsed(next);
    localStorage.setItem(sidebarPreferenceKey, String(next));
  };
  return (
    <div
      className={[
        "app-shell",
        collapsed ? "sidebar-collapsed" : "",
        drawerOpen ? "sidebar-open" : "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <button
        type="button"
        className="mobile-menu-button"
        aria-label={t("openNavigation")}
        aria-controls="primary-sidebar"
        aria-expanded={drawerOpen}
        onClick={() => setDrawerOpen(true)}
      >
        <Icon name="menu" />
      </button>
      <aside className="sidebar" id="primary-sidebar" ref={sidebarRef} role={drawerOpen ? "dialog" : undefined} aria-modal={drawerOpen || undefined} aria-label={t("primaryNavigation")}>
        <button
          type="button"
          className="mobile-sidebar-close"
          aria-label={t("closeNavigation")}
          onClick={() => setDrawerOpen(false)}
        >
          <Icon name="close" />
        </button>
        <div className="brand">
          <div className="brand-mark">TX</div>
          <div className="brand-copy">
            <strong>TradingAgentsX</strong>
            <small>{t("brandTagline")}</small>
          </div>
        </div>
        <Link className="button primary global-new-research" to="/runs/new" onClick={() => setDrawerOpen(false)}><Icon name="newRun" /><span className="nav-label">{t("newRun")}</span></Link>
        <nav aria-label={t("primaryNavigation")}>
          {nav.map((item) => (
            <Link
              key={item.to}
              to={item.to}
              className={isNavActive(pathname, item.to) ? "active" : ""}
              onClick={() => setDrawerOpen(false)}
              title={collapsed ? t(item.key) : undefined}
            >
              <span className="nav-icon" aria-hidden="true">
                <Icon name={item.key} />
              </span>
              <span className="nav-label">{t(item.key)}</span>
            </Link>
          ))}
        </nav>
        <button
          type="button"
          className="sidebar-collapse-button"
          aria-label={t(collapsed ? "expandSidebar" : "collapseSidebar")}
          aria-expanded={!collapsed}
          onClick={toggleCollapsed}
        >
          <Icon name="menu" />
          <span className="nav-label">
            {t(collapsed ? "expandSidebar" : "collapseSidebar")}
          </span>
        </button>
        <div className="sidebar-foot">
          <label htmlFor="locale">{t("language")}</label>
          <select
            id="locale"
            value={i18n.language}
            onChange={(event) => changeLocale(event.target.value)}
          >
            <option value="zh-CN">简体中文</option>
            <option value="en">English</option>
            <option value="ja">日本語</option>
          </select>
          <small>{t("researchOnly")}</small>
        </div>
      </aside>
      {drawerOpen && (
        <button
          type="button"
          className="sidebar-backdrop"
          aria-label={t("closeNavigation")}
          onClick={() => setDrawerOpen(false)}
        />
      )}
      <main className="main-content">{children}</main>
    </div>
  );
}

function isNavActive(pathname: string, target: string) {
  if (target === "/") return pathname === "/";
  if (target === "/runs/new") return pathname === target;
  if (target === "/runs") {
    return (
      pathname === target ||
      (pathname.startsWith("/runs/") && pathname !== "/runs/new")
    );
  }
  return pathname === target || pathname.startsWith(`${target}/`);
}
