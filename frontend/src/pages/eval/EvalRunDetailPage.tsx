/**
 * EvalRunDetailPage — shows a single evaluation run with progress and results.
 */
import { Alert, Card, Progress, Space, Spin, Statistic, Table, Tag, Typography } from "antd";
import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";

import { fetchEvalRun } from "@/api/eval";
import type { EvalCaseResult, EvalRunDetail } from "@/types/eval";

const { Text, Title } = Typography;

export default function EvalRunDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const [run, setRun] = useState<EvalRunDetail | null>(null);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    if (!runId) return;
    setLoading(true);
    try {
      const detail = await fetchEvalRun(runId);
      setRun(detail);
    } catch (err) {
      // Silent fail; keep polling.
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, [runId]);

  useEffect(() => {
    if (!run || run.status !== "running") return;
    const timer = setInterval(() => {
      void load();
    }, 2000);
    return () => clearInterval(timer);
  }, [run]);

  if (!run) {
    return (
      <Card size="small">
        <Spin spinning style={{ display: "block", margin: "40px 0" }} />
      </Card>
    );
  }

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="large">
      {run.error && (
        <Alert message="运行错误" description={run.error} type="error" showIcon />
      )}

      <Card size="small">
        <Title level={5} style={{ marginTop: 0 }}>
          {run.dataset}
        </Title>
        <Progress
          percent={
            run.total ? Math.round((run.processed / run.total) * 100) : 0
          }
          status={
            run.status === "failed"
              ? "exception"
              : run.status === "completed"
                ? "success"
                : "active"
          }
          showInfo
        />
        <Space wrap>
          <Statistic title="总数" value={run.total} valueStyle={{ fontSize: 14 }} />
          <Statistic title="通过" value={run.passed} valueStyle={{ fontSize: 14 }} />
          <Statistic title="失败" value={run.errored} valueStyle={{ fontSize: 14 }} />
          <Statistic
            title="准确率"
            value={Math.round((run.metrics.accuracy || 0) * 100)}
            suffix="%"
            valueStyle={{ fontSize: 14 }}
          />
          {run.metrics.tool_call_accuracy !== undefined && (
            <Statistic
              title="工具调用准确率"
              value={Math.round((run.metrics.tool_call_accuracy ?? 0) * 100)}
              suffix="%"
              valueStyle={{ fontSize: 14 }}
            />
          )}
          {run.metrics.relevance_accuracy !== undefined && (
            <Statistic
              title="相关性准确率"
              value={Math.round((run.metrics.relevance_accuracy ?? 0) * 100)}
              suffix="%"
              valueStyle={{ fontSize: 14 }}
            />
          )}
        </Space>
      </Card>

      <Card title="Case 结果" size="small" loading={loading && run.status === "running"}>
        <Table
          size="small"
          rowKey="result_id"
          pagination={{ pageSize: 10 }}
          dataSource={run.results}
          expandable={{
            expandedRowRender: (record: EvalCaseResult) => (
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
                <Tag color={passed ? "success" : "error"}>
                  {passed ? "通过" : "未通过"}
                </Tag>
              ),
            },
          ]}
        />
      </Card>
    </Space>
  );
}
