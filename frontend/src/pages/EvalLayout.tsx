/**
 * EvalLayout — layout shell for the evaluation sub-application.
 */
import { ExperimentOutlined } from "@ant-design/icons";
import { Breadcrumb, Layout, Space, Typography } from "antd";
import { Link, Outlet, useParams } from "react-router-dom";

const { Header, Content } = Layout;
const { Text, Title } = Typography;

export default function EvalLayout() {
  const { datasetName, runId } = useParams<{ datasetName?: string; runId?: string }>();

  const items = [{ title: <Link to="/eval">评测集列表</Link> }];
  if (datasetName) {
    items.push({
      title: <Link to={`/eval/datasets/${datasetName}`}>{datasetName}</Link>,
    });
  }
  if (runId && datasetName) {
    items.push({ title: <Text>{`运行 ${runId.slice(0, 8)}`}</Text> });
  }

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
        <Breadcrumb style={{ marginBottom: 16 }} items={items} />
        <Outlet />
      </Content>
    </Layout>
  );
}
