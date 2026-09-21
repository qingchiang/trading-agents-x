import { lazy, Suspense, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import Layout from "./Layout";
import { Link, usePathname } from "./router";

const Dashboard = lazy(() => import("./Dashboard"));
const NewRun = lazy(() => import("../features/research/NewRun"));
const RunDetail = lazy(() => import("../features/runs/RunDetail"));
const Runs = lazy(() => import("../features/runs/Runs"));
const Settings = lazy(() => import("../features/settings/Settings"));
const Timeline = lazy(() => import("../features/research/Timeline"));
const LoginDialog = lazy(() => import("./LoginDialog"));

function LoadingFallback() {
  const { t } = useTranslation();
  return <div className="loading" role="status">{t("loading")}</div>;
}

export default function App() {
  const { t } = useTranslation();
  const [authRequired, setAuthRequired] = useState(false);
  const pathname = usePathname();
  useEffect(() => {
    const requireAuth = () => setAuthRequired(true);
    window.addEventListener("tradingagents:auth-required", requireAuth);
    return () =>
      window.removeEventListener("tradingagents:auth-required", requireAuth);
  }, []);

  const page =
    pathname === "/runs/new" ? (
      <NewRun />
    ) : pathname === "/runs" ? (
      <Runs />
    ) : pathname === "/timelines" ? (
      <Timeline />
    ) : /^\/timelines\/[^/]+\/?$/.test(pathname) ? (
      <Timeline />
    ) : /^\/runs\/[^/]+\/?$/.test(pathname) ? (
      <RunDetail />
    ) : (pathname === "/settings" || pathname.startsWith("/settings/")) ? (
      <Settings />
    ) : pathname === "/" ? (
      <Dashboard />
    ) : <section><h1>{t("notFound")}</h1><Link to="/timelines">{t("backToResearch")}</Link></section>;

  return (
    <>
      <Layout>
        <Suspense fallback={<LoadingFallback />}>
          {page}
        </Suspense>
      </Layout>
      {authRequired && (
        <Suspense fallback={null}>
          <LoginDialog
            onAuthenticated={() => {
              setAuthRequired(false);
              window.location.reload();
            }}
          />
        </Suspense>
      )}
    </>
  );
}
