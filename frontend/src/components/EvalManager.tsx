/**
 * EvalManager — core evaluation management UI (datasets, runs, results).
 *
 * This component is layout-agnostic: it can be rendered inside a Drawer via
 * EvalPanel or as a standalone page via EvalPage.
 */
import { PlayCircleOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Progress,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Typography,
} from "antd";
import { useState } from "react";

import type {
  EvalDataset,
  EvalRunDetail,
  EvalRunSummary,
} from "@/types/eval";

const { Text, Title } = Typography;

export interface EvalManagerProps {
  loading: boolean;
  datasets: EvalDataset[];
  runs: EvalRunSummary[];
  selectedRunId: string | null;
  runDetail: EvalRunDetail | null;
  error: string | null;
  loadDatasets: () => Promise<void>;
  loadRuns: () => Promise<void>;
  startRun: (dataset: string) => Promise<void>;
  selectRun: (runId: string) => Promise<void>;
}

export function EvalManager({
  loading,
  datasets,
  runs,
  selectedRunId,
  runDetail,
  error,
  loadDatasets,
  startRun,
  selectRun,
}: EvalManagerProps) {
  const [selectedDataset, setSelectedDataset] = useState<string | null>(null);

  return (
    <>
      {error && (
        <Alert
          message="评测出错"
          description={error}
          type="error"
          showIcon
          closable
          style={{ marginBottom: 16 }}
        />
      )}

      <Card size="small" style={{ marginBottom: 16 }}>
        <Space>
          <Select
            placeholder="选择评测集"
            style={{ minWidth: 320 }}
            value={selectedDataset}
            onDropdownVisibleChange={(open) => {
              if (open) void loadDatasets();
            }}
            onChange={(value) => {
              if (typeof value === "string") {
                setSelectedDataset(value);
              }
            }}
            disabled={loading}
            options={datasets.map((d) => ({ label: d.name, value: d.name }))}
          />
          <Button
            type="primary"
            icon={<PlayCircleOutlined />}
            loading={loading}
            disabled={!selectedDataset}
            onClick={() => {
              if (selectedDataset) {
                void startRun(selectedDataset);
              }
            }}
          >
            运行评测
          </Button>
        </Space>
      </Card>

      <div style={{ display: "flex", gap: 16 }}>
        <Card
          title="评测历史"
          size="small"
          style={{ width: 360, flexShrink: 0 }}
          bodyStyle={{ padding: 0 }}
        >
          <Table
            size="small"
            rowKey="run_id"
            pagination={false}
            loading={loading && runs.length === 0}
            dataSource={runs}
            onRow={(record) => ({
              onClick: () => selectRun(record.run_id),
              style: {
                cursor: "pointer",
                background:
                  record.run_id === selectedRunId ? "#e6f4ff" : undefined,
              },
            })}
            columns={[
              {
                title: "数据集",
                dataIndex: "dataset",
                key: "dataset",
                ellipsis: true,
              },
              {
                title: "状态",
                dataIndex: "status",
                key: "status",
                width: 80,
                render: (status: string) => {
                  const color =
                    status === "completed"
                      ? "success"
                      : status === "failed"
                        ? "error"
                        : status === "running"
                          ? "processing"
                          : "default";
                  return <Text type={color as any}>{status}</Text>;
                },
              },
              {
                title: "进度",
                dataIndex: "processed",
                key: "progress",
                width: 80,
                render: (_: unknown, record) => (
                  <Text type="secondary">
                    {record.processed}/{record.total}
                  </Text>
                ),
              },
            ]}
          />
        </Card>

        <div style={{ flex: 1, minWidth: 0 }}>
          {!selectedRunId ? (
            <Card size="small">
              <Text type="secondary">选择左侧评测运行查看详情</Text>
            </Card>
          ) : !runDetail ? (
            <Card size="small">
              <Spin spinning style={{ display: "block", margin: "24px 0" }} />
            </Card>
          ) : (
            <Space direction="vertical" style={{ width: "100%" }} size="middle">
              <Card size="small">
                <Title level={5} style={{ marginTop: 0 }}>
                  {runDetail.dataset}
                </Title>
                <Progress
                  percent={
                    runDetail.total
                      ? Math.round((runDetail.processed / runDetail.total) * 100)
                      : 0
                  }
                  status={
                    runDetail.status === "failed"
                      ? "exception"
                      : runDetail.status === "completed"
                        ? "success"
                        : "active"
                  }
                  showInfo
                />
                <Space wrap>
                  <Statistic
                    title="总数"
                    value={runDetail.total}
                    valueStyle={{ fontSize: 14 }}
                  />
                  <Statistic
                    title="通过"
                    value={runDetail.passed}
                    valueStyle={{ fontSize: 14 }}
                  />
                  <Statistic
                    title="失败"
                    value={runDetail.errored}
                    valueStyle={{ fontSize: 14 }}
                  />
                  <Statistic
                    title="准确率"
                    value={Math.round((runDetail.metrics.accuracy || 0) * 100)}
                    suffix="%"
                    valueStyle={{ fontSize: 14 }}
                  />
                </Space>
              </Card>

              <Card title="Case 结果" size="small">
                <Table
                  size="small"
                  rowKey="result_id"
                  pagination={{ pageSize: 10 }}
                  dataSource={runDetail.results}
                  expandable={{
                    expandedRowRender: (record) => (
                      <div style={{ margin: 0 }}>
                        {record.error && (
                          <Alert
                            message="执行错误"
                            description={record.error}
                            type="error"
                            style={{ marginBottom: 8 }}
                          />
                        )}
                        {Object.entries(record.scores).map(([name, score]) => (
                          <div key={name} style={{ marginBottom: 8 }}>
                            <Text strong>{name}</Text>
                            <br />
                            <Text type={score.passed ? "success" : "danger"}>
                              {score.passed ? "通过" : "未通过"}: {score.reason}
                            </Text>
                          </div>
                        ))}
                        <div
                          className="markdown-body"
                          style={{ maxHeight: 240, overflow: "auto" }}
                        >
                          <pre
                            style={{
                              whiteSpace: "pre-wrap",
                              wordBreak: "break-word",
                            }}
                          >
                            {record.answer}
                          </pre>
                        </div>
                      </div>
                    ),
                  }}
                  columns={[
                    {
                      title: "#",
                      dataIndex: "case_index",
                      key: "case_index",
                      width: 50,
                    },
                    {
                      title: "问题",
                      dataIndex: "question",
                      key: "question",
                      ellipsis: true,
                    },
                    {
                      title: "结果",
                      dataIndex: "passed",
                      key: "passed",
                      width: 80,
                      render: (passed: boolean) => (
                        <Text type={passed ? "success" : "danger"}>
                          {passed ? "通过" : "未通过"}
                        </Text>
                      ),
                    },
                  ]}
                />
              </Card>
            </Space>
          )}
        </div>
      </div>
    </>
  );
}
