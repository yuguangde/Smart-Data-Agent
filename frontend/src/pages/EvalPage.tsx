/**
 * EvalPage — standalone evaluation management application.
 *
 * Accessed at /eval. This is treated as a separate application from the main
 * chat UI; there is no navigation back to the chat app here.
 */
import { ExperimentOutlined } from "@ant-design/icons";
import { Layout, Space, Typography } from "antd";

import { EvalManager } from "@/components/EvalManager";
import { useEvalStore } from "@/store/useEvalStore";

const { Header, Content } = Layout;
const { Title } = Typography;

export default function EvalPage() {
  const evalStore = useEvalStore();
  const { visible, openPanel, closePanel, ...managerProps } = evalStore;

  return (
    <Layout className="app-root" style={{ minHeight: "100vh" }}>
      <Header className="app-header">
        <Space align="center" size={12}>
          <ExperimentOutlined style={{ fontSize: 22, color: "#fff" }} />
          <Title level={4} style={{ color: "#fff", margin: 0 }}>
            评测管理
          </Title>
        </Space>
      </Header>
      <Content style={{ padding: 16 }}>
        <EvalManager {...managerProps} />
      </Content>
    </Layout>
  );
}
