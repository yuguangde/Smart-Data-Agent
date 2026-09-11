/**
 * Entry point — mounts the React tree and wires up Ant Design message
 * theming via `App` from antd.
 *
 * Two top-level applications are exposed:
 *   /                 -> main chat application
 *   /eval/*           -> evaluation management application
 */
import { App as AntdApp } from "antd";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";

import App from "@/App";
import EvalDatasetDetailPage from "@/pages/eval/EvalDatasetDetailPage";
import EvalDatasetListPage from "@/pages/eval/EvalDatasetListPage";
import EvalRunDetailPage from "@/pages/eval/EvalRunDetailPage";
import EvalLayout from "@/pages/EvalLayout";
import "@/style/global.css";

function Root() {
  return (
    <AntdApp>
      <Routes>
        <Route path="/" element={<App />} />
        <Route path="/eval" element={<EvalLayout />}>
          <Route index element={<EvalDatasetListPage />} />
          <Route path="datasets/:datasetName" element={<EvalDatasetDetailPage />} />
          <Route path="runs/:runId" element={<EvalRunDetailPage />} />
        </Route>
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
