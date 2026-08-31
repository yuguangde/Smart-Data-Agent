/**
 * Sidebar — left rail with the conversation list and "New Chat" button.
 * Built on top of `@ant-design/x` `Conversations`.
 */
import { PlusOutlined } from "@ant-design/icons";
import { Conversations } from "@ant-design/x";
// `Conversation` is the prop type accepted by `<Conversations items={...} />`.
import type { Conversation as ConversationItem } from "@ant-design/x";
import { Button, Space, Typography } from "antd";
import type { Conversation } from "@/types/chat";

const { Text } = Typography;

interface Props {
  conversations: Conversation[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}

export function Sidebar({
  conversations,
  activeId,
  onSelect,
  onNew,
  onDelete,
}: Props) {
  const items: ConversationItem[] = conversations.map((c) => ({
    key: c.id,
    label: c.title || "新会话",
    timestamp: c.updatedAt,
    group: c.updatedAt > Date.now() - 24 * 3600 * 1000 ? "今天" : "更早",
  }));

  return (
    <div className="sidebar-root">
      <div className="sidebar-header">
        <Space direction="vertical" size={4} style={{ width: "100%" }}>
          <Text strong style={{ fontSize: 16 }}>
            Smart Data Agent
          </Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            LangGraph · Streaming · SSE
          </Text>
        </Space>
      </div>

      <div style={{ padding: "8px 12px" }}>
        <Button
          block
          type="primary"
          icon={<PlusOutlined />}
          onClick={onNew}
        >
          新建会话
        </Button>
      </div>

      <Conversations
        items={items}
        activeKey={activeId ?? undefined}
        onActiveChange={(k) => onSelect(String(k))}
        style={{ flex: 1, overflowY: "auto", padding: "0 8px" }}
        menu={(conversation) => ({
          items: [
            {
              key: "delete",
              label: "删除",
              danger: true,
              onClick: () => onDelete(conversation.key),
            },
          ],
        })}
      />
    </div>
  );
}