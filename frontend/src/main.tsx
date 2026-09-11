/**
 * Entry point — mounts the React tree and wires up Ant Design message
 * theming via `App` from antd.
 *
 * Two top-level routes are exposed:
 *   /        -> main chat application
 *   /eval    -> standalone evaluation application
 */
import { App as AntdApp } from "antd";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import App from "@/App";
import EvalPage from "@/pages/EvalPage";
import "@/style/global.css";

function Root() {
  return (
    <AntdApp>
      <Routes>
        <Route path="/" element={<App />} />
        <Route path="/eval" element={<EvalPage />} />
      </Routes>
    </AntdApp>
  );
}

const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("#root element not found in index.html");
}

ReactDOM.createRoot(rootEl).render(
  <React.StrictMode>
    <BrowserRouter>
      <Root />
    </BrowserRouter>
  </React.StrictMode>,
);
