/**
 * Entry point — mounts the React tree and wires up Ant Design message
 * theming via `App` from antd (so `message.success()` etc. inherit the
 * configured theme).
 */
import { App as AntdApp } from "antd";
import React from "react";
import ReactDOM from "react-dom/client";

import App from "@/App";
import "@/style/global.css";

const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("#root element not found in index.html");
}

ReactDOM.createRoot(rootEl).render(
  <React.StrictMode>
    <AntdApp>
      <App />
    </AntdApp>
  </React.StrictMode>,
);