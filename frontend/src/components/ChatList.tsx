/**
 * ChatList — message bubble list powered by @ant-design/x `Bubble`.
 *
 * Each message renders as a Bubble; tool calls captured during the
 * assistant turn are rendered as compact collapsible cards beneath the
 * final assistant bubble.
 */
import {
  CheckCircleOutlined,
  LoadingOutlined,
  RobotOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { Bubble } from "@ant-design/x";
import { Avatar, Button, Card, Empty, Space, Typography } from "antd";
import { useEffect, useRef, useState } from "react";
import type { ChatMessage, ToolCall } from "@/types/chat";
import {
  ChartIframe,
  ChartMarkdown,
  extractChartProxyUrl,
} from "./ChartRenderer";

const { Text } = Typography;

interface Props {
  messages: ChatMessage[];
  loading: boolean;
}

export function ChatList({ messages, loading }: Props) {
  const scrollRef = useRef<HTMLDivElement | null>(null);

  // Auto-scroll to the latest message whenever the list or its content
  // changes. Solid enough for a small app; if we needed stability during
  // streaming we could throttle this with rAF.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages, loading]);

  if (messages.length === 0) {
    return (
      <div className="chat-empty" ref={scrollRef}>
        <Empty
          image={<RobotOutlined style={{ fontSize: 56, color: "#1677ff" }} />}
          description={
            <Space direction="vertical" size={4}>
              <Text strong>开启一段对话</Text>
              <Text type="secondary">
                你的智能数据助手，基于 LangGraph + Ant Design X 构建
              </Text>
            </Space>
          }
        />
      </div>
    );
  }

  return (
    <div className="chat-scroll" ref={scrollRef}>
      <Bubble.List
        style={{ padding: 16 }}
        items={messages.map((m) => {
          const isUser = m.role === "user" || m.role === "human";
          return {
            content: m.content,
            role: isUser ? "user" : "assistant",
            loading: m.streaming && !m.content,
            avatar: isUser ? (
              <Avatar icon={<UserOutlined />} />
            ) : (
              <Avatar
                icon={<RobotOutlined />}
                style={{ backgroundColor: "#1677ff" }}
              />
            ),
            // `Bubble.List` types vary; keep the raw message for nested render.
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            ...({ meta: m } as any),
            messageRender: (content: string) => (
              <MessageBubble content={content} message={m} />
            ),
          };
        })}
      />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Internal subcomponents

function MessageBubble({
  content,
  message,
}: {
  content: string;
  message: ChatMessage;
}) {
  return (
    <div className="bubble-body">
      <div
        className="markdown-body"
        style={{ marginBottom: message.toolCalls?.length ? 8 : 0 }}
      >
        <ChartMarkdown content={content} />
        {message.streaming ? <span className="caret" /> : null}
      </div>

      {message.toolCalls && message.toolCalls.length > 0 ? (
        <Space direction="vertical" style={{ width: "100%" }} size={6}>
          {message.toolCalls.map((tc, idx) => (
            <ToolCallCard key={tc.id ?? `${tc.name}-${idx}`} tc={tc} />
          ))}
        </Space>
      ) : null}
    </div>
  );
}

function ToolCallCard({ tc }: { tc: ToolCall }) {
  const [expanded, setExpanded] = useState(false);
  const finished = !!tc.output;

  return (
    <Card
      size="small"
      className="tool-call-card"
      styles={{ body: { padding: "8px 12px" } }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          minHeight: 24,
        }}
      >
        {finished ? (
          <CheckCircleOutlined
            style={{ color: "#52c41a", fontSize: 16 }}
          />
        ) : (
          <LoadingOutlined style={{ color: "#1677ff", fontSize: 16 }} />
        )}
        <Text
          strong
          style={{
            flex: 1,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {tc.name}
        </Text>
        <Text type="secondary" style={{ fontSize: 12, flexShrink: 0 }}>
          {finished ? "已完成" : "调用中..."}
        </Text>
        <Button
          type="link"
          size="small"
          style={{ padding: 0, fontSize: 12, flexShrink: 0 }}
          onClick={() => setExpanded((v) => !v)}
        >
          {expanded ? "收起" : "详情"}
        </Button>
      </div>

      {expanded && (tc.input || tc.output) ? (
        <div style={{ marginTop: 8 }}>
          {tc.input ? (
            <div style={{ marginBottom: 8 }}>
              <Text type="secondary" style={{ fontSize: 12 }}>
                输入参数
              </Text>
              <pre className="tool-call-pre">
                {JSON.stringify(tc.input, null, 2)}
              </pre>
            </div>
          ) : null}
          {tc.output ? (
            <div>
              <Text type="secondary" style={{ fontSize: 12 }}>
                输出结果
              </Text>
              {(() => {
                const chartUrl = extractChartProxyUrl(tc.output);
                return chartUrl ? (
                  <div style={{ marginTop: 4 }}>
                    <ChartIframe src={chartUrl} />
                  </div>
                ) : (
                  <pre className="tool-call-pre">{tc.output}</pre>
                );
              })()}
            </div>
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}