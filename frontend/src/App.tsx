import { lazy, Suspense, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import Layout from "./components/Layout";
import { usePathname } from "./router";

const Dashboard = lazy(() => import("./pages/Dashboard"));
const LoginDialog = lazy(() => import("./components/LoginDialog"));
const NewRun = lazy(() => import("./pages/NewRun"));
const ResearchChainDetail = lazy(() => import("./pages/ResearchChainDetail"));
const ResearchChains = lazy(() => import("./pages/ResearchChains"));
const ResearchReview = lazy(() => import("./pages/ResearchReview"));
const RunDetail = lazy(() => import("./pages/RunDetail"));
const Runs = lazy(() => import("./pages/Runs"));
const Settings = lazy(() => import("./pages/Settings"));

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
    ) : pathname === "/research" ? (
      <ResearchChains />
    ) : /^\/research\/[^/]+\/?$/.test(pathname) ? (
      <ResearchChainDetail />
    ) : pathname === "/runs" ? (
      <Runs />
    ) : /^\/runs\/[^/]+\/?$/.test(pathname) ? (
      <RunDetail />
    ) : pathname === "/reviews" ? (
      <ResearchReview />
    ) : pathname === "/settings" ? (
      <Settings />
    ) : (
      <Dashboard />
    );

  return (
    <>
      <Layout>
        <Suspense fallback={<div className="loading">{t("loading")}</div>}>
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
