/**
 * Entry point — mounts the React tree and wires up Ant Design message
 * theming via `App` from antd (so `message.success()` etc. inherit the
 * configured theme).
 *
 * Simple hash-based routing is used so evaluation gets its own address:
 *   /             -> main chat app
 *   /#/eval       -> evaluation management page
 */
import { App as AntdApp } from "antd";
import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";

import App from "@/App";
import EvalPage from "@/pages/EvalPage";
import "@/style/global.css";

function getRoute(): string {
  return window.location.hash.replace(/^#/, "").replace(/^\//, "").split("/")[0] || "";
}

function Root() {
  const [route, setRoute] = useState(getRoute);

  useEffect(() => {
    const handler = () => setRoute(getRoute());
    window.addEventListener("hashchange", handler);
    return () => window.removeEventListener("hashchange", handler);
  }, []);

  return (
    <AntdApp>
      {route === "eval" ? <EvalPage /> : <App />}
    </AntdApp>
  );
}

const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("#root element not found in index.html");
}

ReactDOM.createRoot(rootEl).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
);
