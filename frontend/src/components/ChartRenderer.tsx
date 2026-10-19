/**
 * ChartRenderer — turns a local Plotly HTML file URL into an inline chart.
 *
 * MCP chart tools return a string containing a ``file://`` path to a generated
 * HTML file. The browser cannot load ``file://`` URLs directly, so we extract
 * the path and request it through the backend proxy ``/api/charts/proxy``.
 */
import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

const CHART_URL_RE = /`?file:\/\/\/[^\s`"'<>]+\.html`?/g;

const API_BASE =
  (import.meta.env?.VITE_API_BASE as string | undefined) || "/api";

/** Build the backend proxy URL for a bare chart file URL. */
export function buildChartProxyUrl(fileUrl: string): string {
  return `${API_BASE}/charts/proxy?url=${encodeURIComponent(fileUrl)}`;
}

/** Return the backend proxy URL for the first chart file URL found in text. */
export function extractChartProxyUrl(output?: string): string | null {
  if (!output) return null;
  const match = output.match(CHART_URL_RE);
  if (!match) return null;
  return buildChartProxyUrl(match[0].replace(/^`|`$/g, ""));
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

export interface ChartPart {
  kind: "text" | "chart";
  value: string;
}

/** Split content into text/chart parts around file:// Plotly HTML URLs. */
export function splitChartParts(content: string): ChartPart[] {
  const parts: ChartPart[] = [];
  let last = 0;
  for (const match of content.matchAll(CHART_URL_RE)) {
    const start = match.index ?? 0;
    const before = content.slice(last, start);
    if (before) {
      parts.push({ kind: "text", value: before });
    }
    const fileUrl = match[0].replace(/^`|`$/g, "");
    parts.push({ kind: "chart", value: fileUrl });
    last = start + match[0].length;
  }
  const tail = content.slice(last);
  if (tail || parts.length === 0) {
    parts.push({ kind: "text", value: tail });
  }
  return parts;
}

interface ChartMarkdownProps {
  content: string;
}

/** Render markdown, replacing chart file URLs with interactive iframes. */
export function ChartMarkdown({ content }: ChartMarkdownProps) {
  const parts = useMemo(() => splitChartParts(content), [content]);

  if (parts.length === 1 && parts[0].kind === "text") {
    return (
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{parts[0].value}</ReactMarkdown>
    );
  }

  return (
    <>
      {parts.map((part, idx) => {
        if (part.kind === "chart") {
          return (
            <div key={`chart-${idx}`} style={{ margin: "8px 0" }}>
              <ChartIframe src={buildChartProxyUrl(part.value)} />
            </div>
          );
        }
        return (
          <ReactMarkdown key={`md-${idx}`} remarkPlugins={[remarkGfm]}>
            {part.value}
          </ReactMarkdown>
        );
      })}
    </>
  );
}
