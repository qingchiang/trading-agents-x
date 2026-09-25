import React from "react";
import ReactDOM from "react-dom/client";
import App from "./app/App";
import "./shared/i18n";
import { Router } from "./app/router";
import "./shared/styles.css";
import "./features/settings/settings.css";
import "./features/research/research.css";
import "./features/research/workspace.css";
import "./features/runs/diagnostics.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Router>
      <App />
    </Router>
  </React.StrictMode>,
);
