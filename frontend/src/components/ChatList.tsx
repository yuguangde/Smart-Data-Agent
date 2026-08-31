/**
 * ChatList — message bubble list powered by @ant-design/x `Bubble`.
 *
 * Each message renders as a Bubble; tool calls captured during the
 * assistant turn are rendered as compact collapsible cards beneath the
 * final assistant bubble.
 */
import {
  CodeOutlined,
  RobotOutlined,
  ToolOutlined,
  UserOutlined,
} from "@ant-design/icons";
import { Bubble } from "@ant-design/x";
import { Avatar, Card, Empty, Space, Tag, Typography } from "antd";
import { useEffect, useRef } from "react";
import type { ChatMessage, ToolCall } from "@/types/chat";

const { Text, Paragraph } = Typography;

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
        items={messages.map((m) => ({
          content: m.content,
          role: m.role === "user" ? "user" : "assistant",
          loading: m.streaming && !m.content,
          avatar:
            m.role === "user" ? (
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
        }))}
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
      <Paragraph
        copyable={!message.streaming}
        style={{ marginBottom: message.toolCalls?.length ? 8 : 0 }}
      >
        {content}
        {message.streaming ? <span className="caret" /> : null}
      </Paragraph>

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
  return (
    <Card
      size="small"
      className="tool-call-card"
      title={
        <Space size={6}>
          {tc.output ? (
            <Tag color="green">已完成</Tag>
          ) : (
            <Tag color="processing">调用中</Tag>
          )}
          <CodeOutlined />
          <Text strong>{tc.name}</Text>
        </Space>
      }
      extra={<ToolOutlined />}
    >
      {tc.input ? (
        <details open>
          <summary style={{ cursor: "pointer", color: "#1677ff" }}>
            输入参数
          </summary>
          <pre className="tool-call-pre">
            {JSON.stringify(tc.input, null, 2)}
          </pre>
        </details>
      ) : null}
      {tc.output ? (
        <details open style={{ marginTop: tc.input ? 8 : 0 }}>
          <summary style={{ cursor: "pointer", color: "#1677ff" }}>
            输出结果
          </summary>
          <pre className="tool-call-pre">{tc.output}</pre>
        </details>
      ) : null}
    </Card>
  );
}