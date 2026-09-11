/**
 * EvalDatasetDetailPage — shows dataset metadata, its cases, and runs.
 */
import { PlayCircleOutlined } from "@ant-design/icons";
import {
  Button,
  Card,
  Descriptions,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import {
  fetchEvalDataset,
  fetchEvalRuns,
  startEvalRun,
} from "@/api/eval";
import type {
  EvalDatasetDetail,
  EvalRunSummary,
} from "@/types/eval";

const { Text, Title } = Typography;

export default function EvalDatasetDetailPage() {
  const { datasetName } = useParams<{ datasetName: string }>();
  const navigate = useNavigate();
  const [dataset, setDataset] = useState<EvalDatasetDetail | null>(null);
  const [runs, setRuns] = useState<EvalRunSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [starting, setStarting] = useState(false);

  const load = async () => {
    if (!datasetName) return;
    setLoading(true);
    try {
      const [ds, rs] = await Promise.all([
        fetchEvalDataset(datasetName),
        fetchEvalRuns(datasetName),
      ]);
      setDataset(ds);
      setRuns(rs);
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [datasetName]);

  const handleStart = async () => {
    if (!datasetName) return;
    setStarting(true);
    try {
      const run = await startEvalRun({ dataset: datasetName });
      message.success("评测已开始");
      navigate(`/eval/runs/${run.run_id}`);
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err));
    } finally {
      setStarting(false);
    }
  };

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="large">
      <Card size="small">
        <Space
          style={{ width: "100%", justifyContent: "space-between" }}
          align="center"
        >
          <Title level={5} style={{ margin: 0 }}>
            {datasetName}
          </Title>
          <Button
            type="primary"
            icon={<PlayCircleOutlined />}
            loading={starting}
            onClick={handleStart}
          >
            运行评测
          </Button>
        </Space>
      </Card>

      {dataset && (
        <Card size="small">
          <Descriptions size="small" column={3}>
            <Descriptions.Item label="路径">{dataset.path}</Descriptions.Item>
            <Descriptions.Item label="大小">
              {(dataset.size_bytes / 1024).toFixed(1)} KB
            </Descriptions.Item>
            <Descriptions.Item label="Case 数量">{dataset.total}</Descriptions.Item>
          </Descriptions>
        </Card>
      )}

      <Card size="small" title="Cases">
        <Table
          rowKey="index"
          loading={loading}
          dataSource={dataset?.cases || []}
          pagination={{ pageSize: 10 }}
          columns={[
            {
              title: "#",
              dataIndex: "index",
              key: "index",
              width: 50,
            },
            {
              title: "问题",
              dataIndex: "question",
              key: "question",
              ellipsis: true,
            },
            {
              title: "期望工具",
              dataIndex: "expected_tool",
              key: "expected_tool",
              width: 120,
              render: (tool: string | null) => tool || "—",
            },
            {
              title: "标准答案 SQL",
              dataIndex: "expected_args",
              key: "gold_sql",
              ellipsis: true,
              render: (args: Record<string, unknown>) => {
                const sql = args?.sql as string | undefined;
                return sql ? (
                  <code style={{ fontSize: 12 }}>{sql}</code>
                ) : (
                  "—"
                );
              },
            },
            {
              title: "期望关键词",
              dataIndex: "expected_in_answer",
              key: "expected_in_answer",
              render: (keywords: string[]) =>
                keywords.length > 0 ? (
                  keywords.map((k) => <Tag key={k}>{k}</Tag>)
                ) : (
                  <Text type="secondary">—</Text>
                ),
            },
            {
              title: "标签",
              dataIndex: "tags",
              key: "tags",
              render: (tags: string[]) =>
                tags.map((t) => <Tag key={t}>{t}</Tag>),
            },
          ]}
        />
      </Card>

      <Card size="small" title="运行历史">
        <Table
          rowKey="run_id"
          loading={loading}
          dataSource={runs}
          pagination={false}
          columns={[
            {
              title: "运行 ID",
              dataIndex: "run_id",
              key: "run_id",
              render: (id: string) => id.slice(0, 12),
            },
            {
              title: "状态",
              dataIndex: "status",
              key: "status",
              width: 100,
              render: (status: string) => {
                const color =
                  status === "completed"
                    ? "success"
                    : status === "failed"
                      ? "error"
                      : status === "running"
                        ? "processing"
                        : "default";
                return <Tag color={color}>{status}</Tag>;
              },
            },
            {
              title: "进度",
              key: "progress",
              width: 100,
              render: (_, record) => (
                <Text type="secondary">
                  {record.processed}/{record.total}
                </Text>
              ),
            },
            {
              title: "准确率",
              key: "accuracy",
              width: 100,
              render: (_, record) => (
                <Text>{Math.round((record.metrics.accuracy || 0) * 100)}%</Text>
              ),
            },
            {
              title: "操作",
              key: "actions",
              width: 100,
              render: (_, record) => (
                <Link to={`/eval/runs/${record.run_id}`}>
                  <Button size="small">详情</Button>
                </Link>
              ),
            },
          ]}
        />
      </Card>
    </Space>
  );
}
