/**
 * EvalPage — standalone evaluation management page.
 *
 * Accessed via the hash route `/#/eval` so the main chat UI stays clean.
 */
import { LeftOutlined } from "@ant-design/icons";
import { Button, Layout, Space, Typography } from "antd";

import { EvalPanel } from "@/components/EvalPanel";
import { useEvalStore } from "@/store/useEvalStore";

const { Header, Content } = Layout;
const { Title } = Typography;

export default function EvalPage() {
  const evalStore = useEvalStore();

  return (
    <Layout className="app-root" style={{ minHeight: "100vh" }}>
      <Header className="app-header">
        <Space align="center" size={12}>
          <Button
            type="text"
            icon={<LeftOutlined />}
            style={{ color: "#fff" }}
            onClick={() => {
              window.location.hash = "#";
            }}
          >
            返回主界面
          </Button>
          <Title level={4} style={{ color: "#fff", margin: 0 }}>
            评测管理
          </Title>
        </Space>
      </Header>
      <Content style={{ padding: 16 }}>
        <EvalPanel {...evalStore} onClose={() => { window.location.hash = "#"; }} />
      </Content>
    </Layout>
  );
}
