/**
 * ContextUsage — small footer showing current thread diagnostics.
 */
import { useEffect, useState } from "react";
import { Typography } from "antd";

import { getThreadContextSize } from "@/api/chat";
import type {
  ChatMessage,
  ThreadContextSizeResponse,
} from "@/types/chat";

const { Text } = Typography;

// Default 128k context window. Override at build time via
// VITE_CONTEXT_WINDOW_TOKENS=200000 in .env if needed.
const CONTEXT_WINDOW_TOKENS =
  Number(
    (import.meta.env?.VITE_CONTEXT_WINDOW_TOKENS as string | undefined) || "",
  ) || 128_000;

function formatK(num: number): string {
  if (num >= 1000) {
    return `${(num / 1000).toFixed(num >= 10000 ? 0 : 1)}k`;
  }
  return String(num);
}

function ratioColor(ratio: number): string {
  if (ratio >= 80) return "#ff4d4f";
  if (ratio >= 50) return "#faad14";
  return "#52c41a";
}

interface Props {
  threadId: string | null;
  messages: ChatMessage[];
  loading: boolean;
}

export function ContextUsage({ threadId, messages, loading }: Props) {
  const [stats, setStats] = useState<ThreadContextSizeResponse | null>(null);

  useEffect(() => {
    if (!threadId) {
      setStats(null);
      return;
    }

    let cancelled = false;
    getThreadContextSize(threadId)
      .then((data) => {
        if (!cancelled) setStats(data);
      })
      .catch((err) => {
        // Silent: this is a diagnostic widget, not critical to chat.
        console.debug("context size fetch failed", err);
      });

    return () => {
      cancelled = true;
    };
  }, [threadId, messages.length, loading]);

  if (!threadId || !stats) {
    return <div style={{ height: 16, marginTop: 4 }} />;
  }

  const total = stats.total_tokens;
  const ratio = Math.min(
    100,
    Math.max(0, Math.round((total / CONTEXT_WINDOW_TOKENS) * 100)),
  );

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "flex-end",
        marginTop: 4,
        height: 16,
      }}
    >
      <Text
        type="secondary"
        style={{ fontSize: 12, whiteSpace: "nowrap" }}
      >
        消息 <strong>{stats.message_count}</strong> · 工具{" "}
        <strong>{stats.tool_call_count}</strong> · 上下文{" "}
        {formatK(total)} / {formatK(CONTEXT_WINDOW_TOKENS)} tokens
        <span style={{ color: ratioColor(ratio), marginLeft: 6 }}>
          （{ratio}%）
        </span>
      </Text>
    </div>
  );
}
