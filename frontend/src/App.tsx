import { lazy, Suspense, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import Layout from "./components/Layout";
import { usePathname } from "./router";

const Dashboard = lazy(() => import("./pages/Dashboard"));
const NewRun = lazy(() => import("./pages/NewRun"));
const RunDetail = lazy(() => import("./pages/RunDetail"));
const Runs = lazy(() => import("./pages/Runs"));
const Settings = lazy(() => import("./pages/Settings"));
const Timeline = lazy(() => import("./pages/Timeline"));
const LoginDialog = lazy(() => import("./components/LoginDialog"));

function LoadingFallback() {
  const { t } = useTranslation();
  return <div className="loading" role="status">{t("loading")}</div>;
}

export default function App() {
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
    ) : pathname === "/settings" ? (
      <Settings />
    ) : (
      <Dashboard />
    );

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
