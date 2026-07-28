import { PropsWithChildren } from "react";
import { useTranslation } from "react-i18next";
import i18n from "../i18n";
import { NavLink } from "../router";

const nav = [
  { to: "/", key: "dashboard", icon: "⌁" },
  { to: "/runs/new", key: "newRun", icon: "+" },
  { to: "/memory", key: "memory", icon: "◫" },
  { to: "/settings", key: "settings", icon: "◇" },
];

export default function Layout({ children }: PropsWithChildren) {
  const { t } = useTranslation();
  const changeLocale = (locale: string) => {
    localStorage.setItem("tradingagents-locale", locale);
    void i18n.changeLanguage(locale);
  };
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">TX</div>
          <div>
            <strong>TradingAgentsX</strong>
            <small>{t("brandTagline")}</small>
          </div>
        </div>
        <nav>
          {nav.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === "/"}
              className={({ isActive }) => (isActive ? "active" : "")}
            >
              <span>{item.icon}</span>
              {t(item.key)}
            </NavLink>
          ))}
        </nav>
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
      <main className="main-content">{children}</main>
    </div>
  );
}
