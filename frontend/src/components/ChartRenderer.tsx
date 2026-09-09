/**
 * ChartRenderer — turns a local Plotly HTML file URL into an inline chart.
 *
 * MCP chart tools return a string containing a ``file://`` path to a generated
 * HTML file. The browser cannot load ``file://`` URLs directly, so we extract
 * the path and request it through the backend proxy ``/api/charts/proxy``.
 */

const CHART_URL_RE = /file:\/\/\/[^\s"'<>]+\.html/;

const API_BASE =
  (import.meta.env?.VITE_API_BASE as string | undefined) || "/api";

/** Return the backend proxy URL for the first chart file URL found in text. */
export function extractChartProxyUrl(output?: string): string | null {
  if (!output) return null;
  const match = output.match(CHART_URL_RE);
  if (!match) return null;
  return `${API_BASE}/charts/proxy?url=${encodeURIComponent(match[0])}`;
}

/** True if the tool output looks like it references a generated chart file. */
export function hasChartUrl(output?: string): boolean {
  return !!output && CHART_URL_RE.test(output);
}

interface ChartIframeProps {
  src: string;
}

export function ChartIframe({ src }: ChartIframeProps) {
  return (
    <iframe
      title="Plotly 图表"
      src={src}
      sandbox="allow-scripts"
      loading="lazy"
      style={{
        width: "100%",
        height: 360,
        border: 0,
        borderRadius: 6,
        background: "#fff",
      }}
    />
  );
}
