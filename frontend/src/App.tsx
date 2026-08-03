import { useEffect, useState } from "react";
import Layout from "./components/Layout";
import LoginDialog from "./components/LoginDialog";
import Dashboard from "./pages/Dashboard";
import Memory from "./pages/Memory";
import NewRun from "./pages/NewRun";
import RunDetail from "./pages/RunDetail";
import Runs from "./pages/Runs";
import Settings from "./pages/Settings";
import { usePathname } from "./router";

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
    ) : /^\/runs\/[^/]+\/?$/.test(pathname) ? (
      <RunDetail />
    ) : pathname === "/memory" ? (
      <Memory />
    ) : pathname === "/settings" ? (
      <Settings />
    ) : (
      <Dashboard />
    );

  return (
    <>
      <Layout>{page}</Layout>
      {authRequired && (
        <LoginDialog
          onAuthenticated={() => {
            setAuthRequired(false);
            window.location.reload();
          }}
        />
      )}
    </>
  );
}
