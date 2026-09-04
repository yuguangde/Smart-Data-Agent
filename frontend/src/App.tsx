/**
 * App — top-level layout for the Smart Data Agent UI.
 *
 * Layout:
 *   ┌─ Header ────────────────────────────────────────────────┐
 *   ├─ Sidebar ──┬─ Chat panel (Welcome | ChatList) ───────────┤
 *   │            │                                            │
 *   │  threads   │              message bubbles               │
 *   │            │                                            │
 *   │            │  ─────────── SenderBox ──────────────────  │
 *   └────────────┴────────────────────────────────────────────┘
 */
import {
  ApiOutlined,
  GithubOutlined,
  RobotOutlined,
} from "@ant-design/icons";
import { Welcome } from "@ant-design/x";
import { Alert, ConfigProvider, Layout, Space, Tag, Typography, theme } from "antd";
import zhCN from "antd/locale/zh_CN";
import "antd/dist/reset.css";

import { ChatList } from "@/components/ChatList";
import { SenderBox } from "@/components/SenderBox";
import { Sidebar } from "@/components/Sidebar";
import { useChatStore } from "@/store/useChatStore";

const { Header, Sider, Content, Footer } = Layout;
const { Title, Text } = Typography;

export default function App() {
  const {
    threadId,
    messages,
    conversations,
    loading,
    pendingHistory,
    pendingApproval,
    approvalPayload,
    error,
    sendMessage,
    approveTool,
    newConversation,
    selectConversation,
    removeConversation,
    stop,
  } = useChatStore();

  const showWelcome = messages.length === 0 && !pendingHistory;

  return (
    <ConfigProvider
      locale={zhCN}
      theme={{
        algorithm: theme.defaultAlgorithm,
        token: { colorPrimary: "#1677ff", borderRadius: 8 },
      }}
    >
      <Layout className="app-root">
        <Header className="app-header">
          <Space align="center" size={12}>
            <RobotOutlined style={{ fontSize: 22, color: "#fff" }} />
            <Title level={4} style={{ color: "#fff", margin: 0 }}>
              Smart Data Agent
            </Title>
            <Tag color="geekblue" style={{ marginLeft: 8 }}>
              v0.1
            </Tag>
          </Space>
          <div style={{ flex: 1 }} />
          <Space>
            <Tag icon={<ApiOutlined />} color="cyan">
              SSE
            </Tag>
            <Text style={{ color: "rgba(255,255,255,0.85)" }}>
              {threadId ? `thread: ${threadId.slice(0, 8)}…` : "尚未开启会话"}
            </Text>
            <a
              href="https://github.com/anthropics/claude-code"
              target="_blank"
              rel="noreferrer"
              style={{ color: "rgba(255,255,255,0.85)" }}
            >
              <GithubOutlined />
            </a>
          </Space>
        </Header>

        <Layout>
          <Sider
            width={280}
            className="app-sider"
            theme="light"
          >
            <Sidebar
              conversations={conversations}
              activeId={threadId}
              onSelect={selectConversation}
              onNew={() => {
                void newConversation();
              }}
              onDelete={removeConversation}
            />
          </Sider>

          <Layout>
            <Content className="app-content">
              {error ? (
                <Alert
                  type="error"
                  showIcon
                  closable
                  message="请求出错"
                  description={error}
                  style={{ margin: 12 }}
                />
              ) : null}

              {showWelcome ? (
                <Welcome
                  className="welcome-pane"
                  icon={<RobotOutlined style={{ fontSize: 56 }} />}
                  title="你好，我是 Smart Data Agent"
                  description="基于 LangGraph 与 Ant Design X 构建。支持流式回复、多会话、工具调用可视化。"
                  style={{ background: "transparent" }}
                />
              ) : (
                <ChatList messages={messages} loading={loading} />
              )}
            </Content>

            <Footer className="app-footer">
              <SenderBox
                loading={loading}
                disabled={pendingHistory}
                pendingApproval={pendingApproval}
                approvalPayload={approvalPayload}
                onSend={(text) => {
                  void sendMessage(text);
                }}
                onApprove={approveTool}
                onStop={stop}
              />
            </Footer>
          </Layout>
        </Layout>
      </Layout>
    </ConfigProvider>
  );
}