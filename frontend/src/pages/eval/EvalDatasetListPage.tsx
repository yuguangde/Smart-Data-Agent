/**
 * EvalDatasetListPage — lists all available evaluation datasets.
 */
import { EyeOutlined, PlayCircleOutlined } from "@ant-design/icons";
import {
  Button,
  Card,
  Space,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { fetchEvalDatasets, fetchEvalRuns, startEvalRun } from "@/api/eval";
import type { EvalDataset, EvalRunSummary } from "@/types/eval";

const { Text, Title } = Typography;

export default function EvalDatasetListPage() {
  const navigate = useNavigate();
  const [datasets, setDatasets] = useState<EvalDataset[]>([]);
  const [runs, setRuns] = useState<EvalRunSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [starting, setStarting] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const [ds, rs] = await Promise.all([
        fetchEvalDatasets(),
        fetchEvalRuns(),
      ]);
      setDatasets(ds);
      setRuns(rs);
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const handleStart = async (dataset: string) => {
    setStarting(dataset);
    try {
      const run = await startEvalRun({ dataset });
      message.success("评测已开始");
      navigate(`/eval/runs/${run.run_id}`);
    } catch (err) {
      message.error(err instanceof Error ? err.message : String(err));
    } finally {
      setStarting(null);
    }
  };

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="large">
      <Card size="small">
        <Title level={5} style={{ marginTop: 0 }}>
          评测集
        </Title>
        <Table
          rowKey="name"
          loading={loading}
          dataSource={datasets}
          pagination={false}
          columns={[
            {
              title: "名称",
              dataIndex: "name",
              key: "name",
              render: (name: string) => <Text strong>{name}</Text>,
            },
            {
              title: "大小",
              dataIndex: "size_bytes",
              key: "size_bytes",
              width: 120,
              render: (bytes: number) => `${(bytes / 1024).toFixed(1)} KB`,
            },
            {
              title: "操作",
              key: "actions",
              width: 200,
              render: (_, record) => (
                <Space>
                  <Link to={`/eval/datasets/${encodeURIComponent(record.name)}`}>
                    <Button size="small" icon={<EyeOutlined />}>
                      查看
                    </Button>
                  </Link>
                  <Button
                    size="small"
                    type="primary"
                    icon={<PlayCircleOutlined />}
                    loading={starting === record.name}
                    onClick={() => handleStart(record.name)}
                  >
                    运行
                  </Button>
                </Space>
              ),
            },
          ]}
        />
      </Card>

      <Card size="small">
        <Title level={5} style={{ marginTop: 0 }}>
          最近运行
        </Title>
        <Table
          rowKey="run_id"
          loading={loading}
          dataSource={runs.slice(0, 10)}
          pagination={false}
          columns={[
            {
              title: "数据集",
              dataIndex: "dataset",
              key: "dataset",
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
              width: 120,
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
